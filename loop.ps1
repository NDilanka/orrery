<#
.SYNOPSIS
  Self-prompting loop: generate -> test -> fix until green. Hand-rolled harness
  around headless `claude -p`. Windows / PowerShell / Bun / Claude Max-aware.
  V1: evaluation-hardened (best-pass tracking, regression rollback, plateau
  detection). Decision logic lives in loopcore.ps1 and is unit-tested by
  selftest.ps1. See loop-engineering.md for rationale.

.PARAMETER TaskFile
  The spec the agent reads each iteration (default TASK.md). The loop is
  task-agnostic: point this at your own module's task file.

.PARAMETER DryRun
  Validate wiring WITHOUT calling claude: checks git, hashes tests, runs the
  gate once, prints the result, exits. No quota spent.

.PARAMETER Fresh
  Reset .loop/progress.md to a clean template before starting (use for a new
  task; omit to preserve state for a resumed run).

.EXAMPLE
  pwsh -File loop.ps1 -DryRun
  pwsh -File loop.ps1 -TaskFile TASK.calc.md -Fresh -MaxIters 10 -CostCeilingUsd 3
#>
[CmdletBinding()]
param(
  [string] $TaskFile        = "TASK.md",
  [int]    $MaxIters        = 15,
  [double] $CostCeilingUsd  = 3.00,    # cumulative USD across ALL iterations
  [int]    $MaxTurns        = 30,      # turns within one claude -p call
  [int]    $StagnationLimit = 2,       # consecutive no-change iters -> handoff
  [int]    $PlateauLimit    = 3,       # consecutive changed-but-no-gain -> handoff
  [int]    $RegressLimit    = 3,       # rollbacks before handoff
  [string] $StateDir        = ".loop",
  [switch] $DryRun,
  [switch] $Fresh
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
. "$PSScriptRoot/loopcore.ps1"

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

$TestHash0 = Get-TestHash
if (-not $TestHash0) { throw "No *.test.ts files found — nothing to gate against." }

$script:Iter = 0
$script:Cum  = 0.0
$Stale       = 0
$Plateau     = 0
$RegressCount = 0

$base       = Invoke-Gate
$BaseTotal  = $base.Total
$BestPass   = $base.Pass
$BestCommit = if ($UseGit) { (git rev-parse HEAD).Trim() } else { $null }
Write-Host "Baseline: $($base.Pass)/$($base.Total) pass  green=$($base.Green)  task=$TaskFile"

if ($DryRun) {
  Write-Host "`nDryRun OK — gate wired. git=$UseGit  bestPass=$BestPass  testHash=$($TestHash0.Substring(0,12))..."
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

for ($script:Iter = 1; $script:Iter -le $MaxIters; $script:Iter++) {
  Write-Host "`n=== iteration $($script:Iter)/$MaxIters  (cum `$$([math]::Round($script:Cum,4))  best $BestPass/$BaseTotal) ===" -ForegroundColor Cyan

  # 3. EXECUTE
  $cliArgs = @(
    '-p', $prompt, '--output-format', 'json', '--max-turns', "$MaxTurns",
    '--permission-mode', 'acceptEdits',
    '--allowedTools', 'Read', 'Edit', 'Write', 'Bash(bun test)', 'Bash(bun test:*)'
  )
  $rawText = & claude @cliArgs 2>$null | Out-String
  try   { $res = $rawText | ConvertFrom-Json }
  catch { Write-Log @{ event="parse_error"; iter=$script:Iter }; Stop-Loop "could not parse claude JSON output" $false }
  if ($res.total_cost_usd) { $script:Cum += [double]$res.total_cost_usd }

  # 4. EVAL GATE + integrity signals
  $g            = Invoke-Gate
  $tampered     = ((Get-TestHash) -ne $TestHash0)
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
