<#
.SYNOPSIS
  Self-prompting loop: generate -> test -> fix until green. Hand-rolled harness
  around headless `claude -p`. Windows / PowerShell / Bun / Claude Max-aware.
  V1: evaluation-hardened (best-pass tracking, regression rollback, plateau
  detection). E1: generalized — multi-stage gate, parametrized lock glob /
  tools / permission mode, concurrency guard, cost-threshold alerts. Decision
  logic lives in loopcore.ps1 and is unit-tested by selftest.ps1. See
  loop-engineering.md for rationale and orrery/PROTOCOL.md for event shapes.

.PARAMETER TaskFile
  The spec the agent reads each iteration (default TASK.md). The loop is
  task-agnostic: point this at your own module's task file.

.PARAMETER GateStages
  Ordered array of gate stages. Each stage is a hashtable
  @{ Name; Command; PassPattern; FailPattern }. Defaults to the single
  `bun test` stage so existing behavior is unchanged. Supply your own to drive
  vitest/jest/pytest/go-test/etc., or to chain codegen+lint+test.

.PARAMETER LockGlob
  Glob for the locked (anti-cheat) files the gate hashes each iteration
  (default *.test.ts). Any change to a matched file = immediate human handoff.

.PARAMETER AllowedTools
  Tools handed to `claude -p` (default: the demo's 6-tool set).

.PARAMETER PermissionMode
  claude --permission-mode value (default acceptEdits).

.PARAMETER DryRun
  Validate wiring WITHOUT calling claude: checks git, hashes tests, runs the
  gate once, prints the result, exits. No quota spent.

.PARAMETER Fresh
  Reset .loop/progress.md to a clean template before starting (use for a new
  task; omit to preserve state for a resumed run).

.PARAMETER AllowConcurrent
  Override the concurrency guard (let two loops share a StateDir). Off by default.

.EXAMPLE
  pwsh -File loop.ps1 -DryRun
  pwsh -File loop.ps1 -TaskFile TASK.calc.md -Fresh -MaxIters 10 -CostCeilingUsd 3
#>
[CmdletBinding()]
param(
  [string]   $TaskFile        = "TASK.md",
  [int]      $MaxIters        = 15,
  [double]   $CostCeilingUsd  = 3.00,    # cumulative USD across ALL iterations
  [int]      $MaxTurns        = 30,      # turns within one claude -p call
  [int]      $StagnationLimit = 2,       # consecutive no-change iters -> handoff
  [int]      $PlateauLimit    = 3,       # consecutive changed-but-no-gain -> handoff
  [int]      $RegressLimit    = 3,       # rollbacks before handoff
  [string]   $StateDir        = ".loop",
  [object[]] $GateStages      = $null,   # default built in loopcore (single bun test)
  [string]   $LockGlob        = '*.test.ts',
  [string[]] $AllowedTools    = @('Read', 'Edit', 'Write', 'Bash(bun test)', 'Bash(bun test:*)'),
  [string]   $PermissionMode  = 'acceptEdits',
  [int[]]    $AlertPct        = @(50, 80, 100),
  [switch]   $DryRun,
  [switch]   $Fresh,
  [switch]   $AllowConcurrent
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
. "$PSScriptRoot/loopcore.ps1"

# Default gate = today's single `bun test` stage. Keeps the demo identical.
if (-not $GateStages -or $GateStages.Count -eq 0) {
  $GateStages = @(
    @{ Name = 'test'; Command = 'bun test'; PassPattern = '(\d+)\s+pass'; FailPattern = '(\d+)\s+fail' }
  )
}

function Write-Log($obj) { ($obj | ConvertTo-Json -Compress) | Add-Content -Path "$StateDir/log.jsonl" }

function Stop-Loop($reason, $green) {
  if (-not $green -and $UseGit -and $BestCommit) { git reset --hard $BestCommit *>$null }  # leave best-known-good tree
  $tag = if ($green) { "[OK]" } else { "[HANDOFF]" }
  $col = if ($green) { "Green" } else { "Yellow" }
  Write-Host "`n$tag $reason" -ForegroundColor $col
  Write-Host ("    iters={0}  cumulative=`${1}  bestPass={2}/{3}" -f $script:Iter, [math]::Round($script:Cum,4), $BestPass, $BaseTotal)
  Write-Log @{ event="stop"; reason=$reason; green=$green; iter=$script:Iter; cum=$script:Cum; bestPass=$BestPass }
  exit ($(if ($green) { 0 } else { 1 }))
}

# --- concurrency guard ------------------------------------------------------
# Refuse to start if another loop.ps1 is already running against this StateDir.
# Enumerate processes by command line; exclude self + parent. -AllowConcurrent
# overrides. Exit code 2 with a clear message on conflict.
function Test-ConcurrentLoop {
  param([string] $StateDirAbs)
  $leaf = Split-Path -Leaf $StateDirAbs
  try {
    $all = Get-CimInstance Win32_Process -ErrorAction Stop
  } catch { return $null }   # CIM unavailable -> can't guard; let it run
  $byId = @{}
  foreach ($p in $all) { $byId[[int]$p.ProcessId] = $p }

  # Exclude the WHOLE ancestor chain of THIS process, not just the immediate
  # parent. The WindowsApps `pwsh.exe` is a launcher stub that spawns the real
  # pwsh as a grandchild, so both stub + launcher carry `loop.ps1` on their
  # command line and would otherwise self-flag.
  $exclude = [System.Collections.Generic.HashSet[int]]::new()
  $cur = $PID
  $guard = 0
  while ($cur -and $byId.ContainsKey([int]$cur) -and $guard -lt 64) {
    [void]$exclude.Add([int]$cur)
    $cur = [int]$byId[[int]$cur].ParentProcessId
    $guard++
  }

  $procs = $all | Where-Object { $_.CommandLine -and $_.CommandLine -match 'loop\.ps1' }
  foreach ($p in $procs) {
    if ($exclude.Contains([int]$p.ProcessId)) { continue }
    $cl = $p.CommandLine
    # Match this StateDir explicitly, OR a loop.ps1 that omits -StateDir (defaults to .loop).
    $sameDir = ($cl -match [regex]::Escape($StateDirAbs)) -or
               ($cl -match ('-StateDir\s+[''"]?' + [regex]::Escape($leaf))) -or
               (($cl -notmatch '-StateDir') -and ($leaf -eq '.loop'))
    if ($sameDir) { return $p }
  }
  return $null
}

if (-not $AllowConcurrent) {
  $stateAbs = if ([System.IO.Path]::IsPathRooted($StateDir)) { $StateDir } else { Join-Path $PSScriptRoot $StateDir }
  $clash = Test-ConcurrentLoop -StateDirAbs $stateAbs
  if ($clash) {
    Write-Host "[GUARD] Another loop.ps1 (PID $($clash.ProcessId)) is already running against StateDir '$StateDir'." -ForegroundColor Red
    Write-Host "        Stop it first, or pass -AllowConcurrent to override." -ForegroundColor Red
    exit 2
  }
}

# --- preflight -------------------------------------------------------------
if (-not (Test-Path $TaskFile)) { throw "TaskFile '$TaskFile' not found." }
if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }
if ($Fresh) {
@"
# Progress

## Status: not started ($TaskFile)

## Done
(none yet)

## Failing / Next
Run ``bun test`` and start from the first failing assertion.

## Notes
Fresh context every iteration — only this file, git history, and
.loop/log.jsonl survive. Keep concise and current.
"@ | Set-Content -Path "$StateDir/progress.md"
}

$UseGit = $false
try { if ((git rev-parse --is-inside-work-tree 2>$null) -eq 'true') { $UseGit = $true } } catch {}

$TestHash0 = Get-TestHash -LockGlob $LockGlob
if (-not $TestHash0) { throw "No files matching '$LockGlob' found — nothing to gate against." }

$script:Iter = 0
$script:Cum  = 0.0
$Stale       = 0
$Plateau     = 0
$RegressCount = 0
$AlertFired  = @()              # cost thresholds already fired (each fires once)

function Invoke-GateAndLog {
  # Run the multi-stage gate, then emit the Protocol `gate` event. Returns $g.
  param([bool] $LogEvent = $true)
  $g = Invoke-Gate -Stages $GateStages
  if ($LogEvent) {
    Write-Log @{ event="gate"; cum=$script:Cum; green=$g.Green; pass=$g.Pass; fail=$g.Fail;
                 total=$g.Total; stages=@($g.Stages | ForEach-Object { @{ name=$_.name; ok=$_.ok; exit=$_.exit } }) }
  }
  $g
}

function Update-Spend {
  # Add this iter's cost, then fire any newly-crossed 50/80/100% alert exactly
  # once. Prints a banner AND emits a Protocol `cost-alert` event.
  param([double] $IterCost)
  if ($IterCost) { $script:Cum += [double]$IterCost }
  $a = Update-CostAlert -Cum $script:Cum -Ceiling $CostCeilingUsd -Thresholds $AlertPct -Fired $script:AlertFired
  $script:AlertFired = $a.Fired
  foreach ($pct in $a.Newly) {
    Write-Host ("  [COST] {0}% of `${1} ceiling — cum=`${2}" -f $pct, $CostCeilingUsd, [math]::Round($script:Cum,4)) -ForegroundColor Magenta
    Write-Log @{ event="cost-alert"; pct=$pct; cum=$script:Cum; ceiling=$CostCeilingUsd }
  }
}

$base       = Invoke-GateAndLog -LogEvent (-not $DryRun)
$BaseTotal  = $base.Total
$BestPass   = $base.Pass
$BestCommit = if ($UseGit) { (git rev-parse HEAD).Trim() } else { $null }
Write-Host "Baseline: $($base.Pass)/$($base.Total) pass  green=$($base.Green)  task=$TaskFile"

if ($DryRun) {
  $stageNames = ($GateStages | ForEach-Object { $_.Name }) -join ','
  Write-Host "`nDryRun OK — gate wired. git=$UseGit  bestPass=$BestPass  testHash=$($TestHash0.Substring(0,12))..."
  Write-Host "    stages=[$stageNames]  lockGlob=$LockGlob  tools=$($AllowedTools.Count)  permMode=$PermissionMode  maxTurns=$MaxTurns"
  Write-Host "No claude calls, no quota spent." -ForegroundColor Cyan
  exit 0
}
if ($base.Green) { Stop-Loop "already green at baseline" $true }

# --- the loop --------------------------------------------------------------
$prompt = @"
Read $TaskFile and .loop/progress.md first.

Then run ``bun test``, read the FIRST failing assertion, and make the SMALLEST
change to the implementation file named in $TaskFile that fixes it. Never edit,
skip, or delete any *.test.ts file, and do not break a test that already passes.
Re-run ``bun test`` to confirm no regression. Finally update .loop/progress.md
with what you changed, what still fails, and the next step. If you are stuck,
write ``BLOCKED: <reason>`` on the first line of .loop/progress.md and stop.
"@

# Build claude args from the parametrized tool set / permission mode.
$toolArgs = @()
foreach ($t in $AllowedTools) { $toolArgs += $t }

for ($script:Iter = 1; $script:Iter -le $MaxIters; $script:Iter++) {
  Write-Host "`n=== iteration $($script:Iter)/$MaxIters  (cum `$$([math]::Round($script:Cum,4))  best $BestPass/$BaseTotal) ===" -ForegroundColor Cyan

  # 3. EXECUTE
  $cliArgs = @(
    '-p', $prompt, '--output-format', 'json', '--max-turns', "$MaxTurns",
    '--permission-mode', $PermissionMode,
    '--allowedTools'
  ) + $toolArgs
  $rawText = & claude @cliArgs 2>$null | Out-String
  try   { $res = $rawText | ConvertFrom-Json }
  catch { Write-Log @{ event="parse_error"; iter=$script:Iter }; Stop-Loop "could not parse claude JSON output" $false }

  # spend accounting + 50/80/100% alerts (must happen before the verdict so the
  # cost-ceiling stop sees the updated cum)
  Update-Spend -IterCost ([double]$res.total_cost_usd)

  # 4. EVAL GATE + integrity signals
  $g            = Invoke-GateAndLog
  $tampered     = ((Get-TestHash -LockGlob $LockGlob) -ne $TestHash0)
  $countDropped = ($g.Total -lt $BaseTotal)
  $blocked      = [bool](Select-String -Path "$StateDir/progress.md" -Pattern '^BLOCKED' -Quiet)
  $changed      = if ($UseGit) { [bool](git status --porcelain) } else { $true }

  # update drift counters before asking for a verdict
  if (-not $changed) { $Stale++ } else { $Stale = 0 }
  if ($changed -and $g.Pass -eq $BestPass) { $Plateau++ } elseif ($g.Pass -ne $BestPass) { $Plateau = 0 }

  $dec = Get-LoopDecision -Green $g.Green -Tampered $tampered -CountDropped $countDropped `
           -Blocked $blocked -Pass $g.Pass -BestPass $BestPass -Changed $changed `
           -RegressCount $RegressCount -RegressLimit $RegressLimit `
           -Plateau $Plateau -PlateauLimit $PlateauLimit -Stale $Stale -StagnationLimit $StagnationLimit `
           -Cum $script:Cum -Ceiling $CostCeilingUsd -Iter $script:Iter -MaxIters $MaxIters

  Write-Log @{ event="iter"; iter=$script:Iter; cost=[double]$res.total_cost_usd; cum=$script:Cum;
               pass=$g.Pass; total=$g.Total; best=$BestPass; changed=$changed;
               stale=$Stale; plateau=$Plateau; regress=$RegressCount; action=$dec.Action; reason=$dec.Reason }
  Write-Host ("  -> {0}/{1} pass  iter_cost=`${2}  cum=`${3}  [{4}]" -f `
              $g.Pass, $g.Total, [double]$res.total_cost_usd, [math]::Round($script:Cum,4), $dec.Action)

  # 6. ACT on the verdict
  switch ($dec.Action) {
    'stop' {
      if ($g.Pass -gt $BestPass) { $BestPass = $g.Pass }   # keep summary/log accurate on one-shot green
      if ($dec.Green -and $UseGit -and $changed) {
        git add -A *>$null; git commit -q -m "loop $($script:Iter): GREEN $($g.Pass)/$($g.Total)" *>$null
      }
      Stop-Loop $dec.Reason $dec.Green
    }
    'rollback' {
      if ($UseGit) { git reset --hard $BestCommit *>$null }
      $RegressCount++
      Write-Host "  rollback -> best $BestPass/$BaseTotal (regress $RegressCount/$RegressLimit)" -ForegroundColor DarkYellow
    }
    'continue' {
      if ($UseGit -and $changed) {
        git add -A *>$null; git commit -q -m "loop $($script:Iter): $($g.Pass)/$($g.Total) pass" *>$null
      }
      if ($g.Pass -gt $BestPass) {                 # new high-water mark
        $BestPass = $g.Pass
        $RegressCount = 0
        if ($UseGit) { $BestCommit = (git rev-parse HEAD).Trim() }
      }
    }
  }
}

Stop-Loop "max iterations ($MaxIters) reached without green" $false
