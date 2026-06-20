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

.PARAMETER Verify
  After the gate goes green, run a SECOND, independent verifier subagent on a
  CHEAP model that sees ONLY the diff + a FROZEN acceptance-criteria contract and
  must try to REFUTE "done". Off by default (no extra quota). When the verdict is
  fail, the loop does NOT stop-green: it feeds the failing criteria back and
  continues (or hands off per the stop rules).

.PARAMETER Contract
  Explicit frozen acceptance-criteria list for the verifier. When omitted, the
  criteria are parsed from the TaskFile's "## Acceptance Criteria" /
  "## Definition of done" section.

.PARAMETER Models
  Per-phase model tiering: a hashtable @{ discover; execute; judge; hard } whose
  values are 'haiku'|'sonnet'|'opus'. Defaults to
  @{ discover='haiku'; execute='sonnet'; judge='haiku'; hard='opus' }. The
  execute tier drives the main iteration; the judge tier drives the verifier;
  the discover tier drives the Q&A auto-decider.

.PARAMETER AutoDecide
  E5. When the agent emits a `QUESTION: <text>` marker (and no UI answer.json is
  waiting), auto-answer it via a cheap-model decider (the discover tier) so an
  unattended overnight run is not blocked. Off by default — without it (and with
  no answer.json) the loop just proceeds. Emits review-question / review-answer.
  The actual decider claude call is ISOLATED in Invoke-DeciderClaude (stubbable).

  Prompt caching is ALWAYS on (it is just call structuring + usage parsing): the
  stable prefix (system instructions + task spec + frozen AC contract) is placed
  first so it is eligible for Anthropic prompt caching; the volatile failing-test
  context goes last. Each iteration emits a `cache {hitRatio,warm}` event parsed
  from the returned JSON usage (tolerant of absence).

.EXAMPLE
  pwsh -File loop.ps1 -DryRun
  pwsh -File loop.ps1 -TaskFile TASK.calc.md -Fresh -MaxIters 10 -CostCeilingUsd 3
  pwsh -File loop.ps1 -Verify -Models @{ execute='sonnet'; judge='haiku' }
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
  [switch]   $Verify,                  # run the anti-false-green verifier subagent
  [string[]] $Contract        = $null, # frozen AC; default = parsed from TaskFile
  [hashtable] $Models         = $null, # per-phase model tiering (defaults below)
  [switch]    $NoQuotaWait,            # E3: disable wait-and-resume (fail fast on a limit)
  [int]       $MaxQuotaWaits = 30,     # E3: give-up backstop on repeated quota waits
  [int]       $DefaultQuotaWaitMin = 30, # E3: wait minutes when no reset time is parseable
  [int]       $IterTimeoutMin = 0,     # E4: per-iteration wall-clock cap (0 = disabled; >0 kills a hung claude)
  [int]       $ConsecutiveFailLimit = 3, # E4: consecutive no-progress iters -> recover-once -> handoff
  [switch]    $AutoDecide,             # E5: auto-answer an agent QUESTION via a cheap-model decider (off by default)
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

# Per-phase model tiering. Merge user overrides over the defaults so a partial
# -Models hashtable (e.g. just @{ judge='haiku' }) still resolves all phases.
$ModelDefaults = @{ discover = 'haiku'; execute = 'sonnet'; judge = 'haiku'; hard = 'opus' }
if ($Models) { foreach ($k in $Models.Keys) { $ModelDefaults[$k] = $Models[$k] } }
$Models = $ModelDefaults
$ExecuteModel = Get-ModelForPhase -Phase 'execute'  -Models $Models
$JudgeModel   = Get-ModelForPhase -Phase 'judge'    -Models $Models
$DeciderModel = Get-ModelForPhase -Phase 'discover' -Models $Models  # E5: Q&A auto-decider tier

function Write-Log($obj) { ($obj | ConvertTo-Json -Compress) | Add-Content -Path "$StateDir/log.jsonl" }

function Invoke-JudgeClaude {
  # ISOLATED, stubbable claude call for the verifier subagent. This is the ONLY
  # place the verifier spends quota; selftest-verify.ps1 never reaches it (it
  # feeds captured strings straight to the parser). A SECOND, independent judge
  # on a CHEAP model that sees ONLY the diff + the frozen contract and must try
  # to REFUTE "done". Returns the judge's raw stdout string.
  param(
    [string]   $Diff,
    [string[]] $Criteria,
    [string]   $Model
  )
  $contractText = ($Criteria | ForEach-Object { "- $_" }) -join "`n"
  $judgePrompt = @"
You are an independent VERIFIER. Your job is to REFUTE the claim that the work is
"done". You see ONLY a git diff and a FROZEN acceptance-criteria contract. Do NOT
assume the tests passing means the criteria are met — check each criterion against
the diff and try to find one that is NOT satisfied.

FROZEN ACCEPTANCE CRITERIA:
$contractText

GIT DIFF:
$Diff

Respond with ONLY a JSON object (no prose) of this exact shape:
{ "pass": <true|false>, "failingCriteria": [<unmet criteria strings>],
  "evidence": "<one-sentence justification>", "nextAction": "<what to do next if failing>" }
Set pass=true ONLY if every criterion is demonstrably satisfied by the diff.
"@
  $judgeArgs = @(
    '-p', $judgePrompt, '--output-format', 'text', '--max-turns', '1',
    '--model', $Model, '--permission-mode', 'plan',
    '--allowedTools', 'Read'
  )
  & claude @judgeArgs 2>$null | Out-String
}

function Invoke-DeciderClaude {
  # E5. ISOLATED, stubbable claude call for the Q&A AUTO-DECIDER. The ONLY place
  # -AutoDecide spends quota; selftest-final.ps1 never reaches it (it tests the
  # marker detection + answer.json consume + event shapes with NO claude). A
  # cheap-model (discover tier) decider that reads ONLY the agent's open question
  # + the task/contract context and returns a SHORT directive answer the next
  # iteration acts on. Plan-mode, read-only, single turn — bounded + cheap.
  # Returns the decider's raw stdout string (the answer text).
  param(
    [string]   $Question,
    [string]   $Task,
    [string[]] $Criteria,
    [string]   $Model
  )
  $contractText = ($Criteria | ForEach-Object { "- $_" }) -join "`n"
  $deciderPrompt = @"
You are the ORCHESTRATOR acting as a cheap DECIDER. The autonomous worker paused
and asked ONE question. Give the SHORTEST actionable directive that unblocks it,
consistent with the task and its frozen acceptance criteria. Do NOT ask a question
back. Answer in one or two sentences of plain text (no JSON, no preamble).

TASK: $Task

FROZEN ACCEPTANCE CRITERIA:
$contractText

WORKER QUESTION:
$Question
"@
  $deciderArgs = @(
    '-p', $deciderPrompt, '--output-format', 'text', '--max-turns', '1',
    '--model', $Model, '--permission-mode', 'plan',
    '--allowedTools', 'Read'
  )
  (& claude @deciderArgs 2>$null | Out-String).Trim()
}

# --- E3: quota survival -----------------------------------------------------
# Two ISOLATED side-effect wrappers — the ONLY places that spend quota / sleep —
# so selftest-resume.ps1 can override them with stubs (NO real claude, NO real
# sleep in tests). All the DECIDING (limited? reset? waitSec? event shapes?) is
# pure and lives in loopcore.ps1.
function Invoke-QuotaProbe {
  # Cheap authoritative probe. Runs `claude -p "ok" --output-format stream-json
  # --max-turns 1`, captures stdout+stderr, and hands the text to the PURE
  # Resolve-QuotaStatus parser. Records the chosen reset on $script:LastQuota.
  # Returns $true when quota is AVAILABLE, $false when LIMITED.
  $errF = [System.IO.Path]::GetTempFileName()
  $praw = (& claude -p "ok" --output-format stream-json --verbose --max-turns 1 2>$errF) | Out-String
  $perr = ""; if (Test-Path $errF) { $perr = Get-Content $errF -Raw; Remove-Item $errF -Force }
  $all  = ($praw + "`n" + $perr)
  $status = Resolve-QuotaStatus -Text $all
  $script:LastQuota = $status
  return (-not $status.Limited)
}

function Start-QuotaSleep([int]$Seconds) {
  # ISOLATED real sleep. Tests override this with a no-op so zero real time passes.
  Start-Sleep -Seconds $Seconds
}

# --- E4: per-iteration wall-clock timeout ----------------------------------
# The ONE place the main execute call spawns a real `claude` process AND the ONE
# place a real wall-clock timer runs. Isolated so selftest-resilience.ps1 stubs
# it (no real claude, no real WaitForExit). Adapted from bmad-loop.ps1's
# Invoke-ClaudeTimed: spawn detached, async-read stdout, WaitForExit(ms); on
# breach kill the WHOLE process tree (taskkill /T /F) and report timedOut.
#
# Returns @{ Raw=<stdout string>; TimedOut=<bool> }. When -TimeoutSec <= 0 the
# wait is unbounded (timeout disabled) — preserves pre-E4 behavior exactly.
function Invoke-ClaudeExecute {
  param([string[]] $CliArgs, [int] $TimeoutSec = 0)
  $claudeExe = (Get-Command claude -ErrorAction SilentlyContinue).Source
  if (-not $claudeExe) { $claudeExe = 'claude' }
  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $claudeExe; $psi.WorkingDirectory = $PSScriptRoot
  foreach ($a in $CliArgs) { [void]$psi.ArgumentList.Add($a) }
  $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
  $proc = [System.Diagnostics.Process]::Start($psi)
  $outTask = $proc.StandardOutput.ReadToEndAsync()
  if ($TimeoutSec -gt 0) {
    if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
      # Hung past the budget — kill the whole tree so no orphaned child lingers.
      try { taskkill /T /F /PID $proc.Id *>$null } catch {}
      try { $proc.Kill() } catch {}
      return @{ Raw = ''; TimedOut = $true }
    }
  } else {
    $proc.WaitForExit()
  }
  $raw = ''
  try { $raw = $outTask.Result } catch {}
  return @{ Raw = $raw; TimedOut = $false }
}

function Wait-ForQuota([string]$Label) {
  # Wait-and-resume loop. PROBE FIRST (a false-positive costs no sleep); on a real
  # limit, emit quota-wait, sleep until reset (+buffer) via the isolated wrappers,
  # re-probe. Emits quota-resume on recovery. Returns $true on resume, $false if
  # MaxQuotaWaits is exhausted. NoQuotaWait short-circuits to a fail-fast $false.
  if ($NoQuotaWait) { return $false }
  for ($i = 1; $i -le $MaxQuotaWaits; $i++) {
    if (Invoke-QuotaProbe) {
      Write-Host "  [QUOTA] quota available — resuming '$Label'" -ForegroundColor Green
      Write-Log (New-QuotaResumeEvent -Label $Label -Probe $i)
      return $true
    }
    $resetAt   = if ($script:LastQuota) { $script:LastQuota.ResetAt } else { $null }
    $resetType = if ($script:LastQuota) { $script:LastQuota.ResetType } else { $null }
    $waitSec   = Get-QuotaWaitSec -ResetAt $resetAt -DefaultWaitMin $DefaultQuotaWaitMin
    $ev        = New-QuotaWaitEvent -Label $Label -Cum $script:Cum -WaitSec $waitSec -Probe $i -ResetType $resetType
    Write-Host ("  [QUOTA] '{0}' paused — sleeping {1}m, resume ~{2} (probe {3}/{4})" -f `
                $Label, [int]($waitSec/60), ([datetime]$ev.resumeAt).ToString('HH:mm'), $i, $MaxQuotaWaits) -ForegroundColor Magenta
    Write-Log $ev
    Start-QuotaSleep -Seconds $waitSec
  }
  return $false
}

function Resolve-Quota([string]$Label) {
  # Called when an iteration's claude call looked like it FAILED. Probe the
  # AUTHORITATIVE rate_limit_info (never phase content). If genuinely limited:
  # emit quota-hit, then run the wait-and-resume loop. Returns $true if the loop
  # may retry the iteration (quota recovered), $false if it should hand off.
  if ($NoQuotaWait) { return $false }
  if (Invoke-QuotaProbe) { return $false }   # not a quota problem — let the caller fail normally
  $resetAt = if ($script:LastQuota) { $script:LastQuota.ResetAt } else { $null }
  Write-Log (New-QuotaHitEvent -Label $Label -Cum $script:Cum -ResetAt $resetAt)
  Write-Host "  [QUOTA] limit hit during '$Label' — entering wait-and-resume" -ForegroundColor Magenta
  return (Wait-ForQuota $Label)
}

function Stop-Loop($reason, $green) {
  if (-not $green -and $UseGit -and $BestCommit) { git reset --hard $BestCommit *>$null }  # leave best-known-good tree
  $tag = if ($green) { "[OK]" } else { "[HANDOFF]" }
  $col = if ($green) { "Green" } else { "Yellow" }
  Write-Host "`n$tag $reason" -ForegroundColor $col
  Write-Host ("    iters={0}  cumulative=`${1}  bestPass={2}/{3}" -f $script:Iter, [math]::Round($script:Cum,4), $BestPass, $BaseTotal)
  Write-Log @{ event="stop"; reason=$reason; green=$green; iter=$script:Iter; cum=$script:Cum; bestPass=$BestPass }
  # E3: write/update checkpoint.json at normal stop too, so the run is legible
  # (a green stop records the certified tree; a handoff records best-known-good).
  $script:CurStage = if ($green) { 'done' } else { 'handoff' }
  try { Write-LoopCheckpoint | Out-Null } catch {}
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

# E2 hardened hash-lock: a PER-FILE map (path->sha256) so tamper detection can
# NAME the changed file. $TestHash0 (the order-stable digest) is derived from it
# for back-compat / the DryRun summary.
$HashMap0  = Get-TestHashMap -LockGlob $LockGlob -BasePath $PSScriptRoot
$TestHash0 = Get-HashMapDigest -Map $HashMap0
if (-not $TestHash0) { throw "No files matching '$LockGlob' found — nothing to gate against." }

# E2 frozen acceptance-criteria contract for the verifier. Explicit -Contract
# wins; otherwise parse the TaskFile's "## Acceptance Criteria" / "## Definition
# of done" section. Frozen ONCE here so the judge can never be steered later.
$FrozenContract = if ($Contract) { @($Contract) } else { @(ConvertTo-ContractCriteria -Text (Get-Content -Path $TaskFile -Raw)) }

# E2 model tiering: announce each phase's chosen model (Protocol `model` event).
# Skipped under -DryRun so a dry run spends no quota AND writes no log lines.
if (-not $DryRun) {
  Write-Log @{ event="model"; phase="execute"; model=$ExecuteModel }
  if ($Verify) { Write-Log @{ event="model"; phase="judge"; model=$JudgeModel } }
}

$script:Iter = 0
$script:Cum  = 0.0
$Stale       = 0
$Plateau     = 0
$RegressCount = 0
$AlertFired  = @()              # cost thresholds already fired (each fires once)
# E4 resilience state:
$IterTimeoutSec = if ($IterTimeoutMin -gt 0) { $IterTimeoutMin * 60 } else { 0 }
$ConsecFail     = 0            # consecutive non-green / no-net-progress iters
$ConsecRecovered = $false     # whether the one-shot recover has been spent this streak
$PlateauAlerted  = $false     # plateau trend-alert is one-shot per plateau episode
$script:LastQuota = $null       # E3: last quota probe result (set by Invoke-QuotaProbe)
$script:CurStage  = 'preflight' # E3: coarse stage label for the checkpoint
# E3: branch + merge-base for the checkpoint. The generic single-goal loop has no
# story branches, so mergeBase = the baseline commit and branch = the current ref.
$script:Branch    = if ($UseGit) { ("" + (git rev-parse --abbrev-ref HEAD 2>$null)).Trim() } else { $null }
$script:MergeBase = if ($UseGit) { ("" + (git rev-parse HEAD 2>$null)).Trim() } else { $null }
# E3: the goal/item label for rollback/handoff events = the TaskFile leaf or 'goal'.
$script:Item = if ($TaskFile) { Split-Path -Leaf $TaskFile } else { 'goal' }

# E3: cooperative-stop + checkpoint resume. The SAFE checkpoint for the generic
# loop is BETWEEN iterations (nothing is killed mid-iteration). Write the exact
# command needed to continue, so a stop is legible and resume = re-running loop.ps1.
function Get-ResumeCommand {
  $a = @("-File", "`"$PSCommandPath`"")
  if ($TaskFile -ne 'TASK.md') { $a += @('-TaskFile', "`"$TaskFile`"") }
  if ($StateDir -ne '.loop')   { $a += @('-StateDir', "`"$StateDir`"") }
  "pwsh " + ($a -join ' ')
}

function Write-LoopCheckpoint {
  # Persist PROTOCOL §7 checkpoint.json (built by the pure New-Checkpoint).
  if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }
  $cp = New-Checkpoint -Stage $script:CurStage -Story $null -Branch $script:Branch `
          -MergeBase $script:MergeBase -CumUsd $script:Cum -Resume (Get-ResumeCommand)
  ($cp | ConvertTo-Json) | Set-Content -Path "$StateDir/checkpoint.json" -Encoding utf8
  $cp
}

function Stop-IfRequested([string]$Scope) {
  # Honor a cooperative STOP flag at a SAFE boundary. $Scope is the boundary we're
  # at: 'story' = between iterations (clean); 'phase' = a within-iteration commit
  # point. A 'story'-mode request is held until a 'story' scope. When honored:
  # commit current work (if git + changed), write the checkpoint, emit
  # cooperative-stop, consume the flag, exit 0 — nothing killed mid-iteration.
  $flag = "$StateDir/STOP"
  $content = if (Test-Path $flag) { Get-Content $flag -Raw -ErrorAction SilentlyContinue } else { $null }
  $req = Get-StopMode -FlagContent $content -Scope $Scope
  if (-not $req.Honor) { return }

  if ($UseGit -and (git status --porcelain)) {
    git add -A *>$null
    git commit -q -m "loop: cooperative stop ($($req.Mode)) at iter $($script:Iter)" *>$null
    $script:Branch    = ("" + (git rev-parse --abbrev-ref HEAD 2>$null)).Trim()
  }
  Write-LoopCheckpoint | Out-Null
  Remove-Item $flag -Force -ErrorAction SilentlyContinue   # consume the request
  $ev = New-CooperativeStopEvent -Scope $Scope -Mode $req.Mode -Stage $script:CurStage `
          -Story $null -Branch $script:Branch -Cum $script:Cum
  Write-Log $ev
  Write-Host "`n[STOPPED] graceful stop honored ($($req.Mode)) at: $($script:CurStage)" -ForegroundColor Cyan
  Write-Host "  Work is safe — committed; git + $StateDir/progress.md are the source of truth." -ForegroundColor Cyan
  Write-Host "  Spent this run: `$$([math]::Round($script:Cum,4)).  Checkpoint: $StateDir/checkpoint.json" -ForegroundColor Cyan
  Write-Host "  Resume anytime:  $(Get-ResumeCommand)" -ForegroundColor White
  exit 0
}

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

function Write-CacheTelemetry {
  # E5. Parse the cache token counters from this iteration's claude result and emit
  # the PROTOCOL §2 `cache {hitRatio,warm}` event so the Orrery tailer can render
  # cache-teal recycled-fuel live. Tolerant of absence (no usage block -> 0/cold).
  # Pure parse via Get-CacheUsage; this wrapper just logs + prints.
  param([object] $Result)
  $usage = if ($Result -and $Result.PSObject -and ($Result.PSObject.Properties.Name -contains 'usage')) { $Result.usage } else { $null }
  $cu = Get-CacheUsage -Usage $usage
  Write-Log (New-CacheEvent -HitRatio $cu.HitRatio -Warm $cu.Warm)
  $warmTag = if ($cu.Warm) { 'warm' } else { 'cold' }
  Write-Host ("  [CACHE] {0} hitRatio={1} (read={2} input={3})" -f $warmTag, $cu.HitRatio, $cu.CacheRead, $cu.Input) -ForegroundColor DarkCyan
  $cu
}

function Resolve-Question {
  # E5 ANSWER INBOX / Q&A surface. Detect an agent QUESTION marker in this iter's
  # result body and/or progress.md (pure Get-QuestionMarker). When found, emit
  # review-question, then resolve the answer in priority order:
  #   (a) <StateDir>/answer.json present + matches this turn -> emit review-answer,
  #       CONSUME (delete) the file, feed the answer back into progress.md;
  #   (b) else if -AutoDecide -> cheap-model decider answers (isolated, stubbable),
  #       emit review-answer, feed it back;
  #   (c) else proceed unanswered (the question is logged; the run continues).
  # Returns the answer string when one was produced (a|b), else $null. The "turn"
  # is the iteration number (the generic loop's Q&A turn key).
  param(
    [int]    $Turn,
    [string] $ResultText
  )
  $progressPath = "$StateDir/progress.md"
  $progressText = if (Test-Path $progressPath) { Get-Content $progressPath -Raw -ErrorAction SilentlyContinue } else { '' }
  # Look in the agent's result body first, then progress.md (first marker wins).
  $q = Get-QuestionMarker -Text $ResultText
  if (-not $q) { $q = Get-QuestionMarker -Text $progressText }
  if (-not $q) { return $null }

  Write-Log (New-ReviewQuestionEvent -Turn $Turn -Q $q)
  Write-Host "  [Q&A] question (turn $Turn): $q" -ForegroundColor Cyan

  # (a) UI answer inbox — answer.json the UI's "answer from UI" wrote.
  $answerPath = "$StateDir/answer.json"
  if (Test-Path $answerPath) {
    $content = Get-Content $answerPath -Raw -ErrorAction SilentlyContinue
    $inbox = Read-AnswerInbox -Content $content -Turn $Turn
    if ($inbox.Matched) {
      Remove-Item $answerPath -Force -ErrorAction SilentlyContinue   # consume once
      Write-Log (New-ReviewAnswerEvent -Turn $Turn -A $inbox.A)
      Write-Host "  [Q&A] answered from UI inbox: $($inbox.A)" -ForegroundColor Green
      Add-Content -Path $progressPath -Value "`n## Answer (turn $Turn, from UI)`nQUESTION: $q`nANSWER: $($inbox.A)`nProceed accordingly; clear the QUESTION line."
      return $inbox.A
    }
  }

  # (b) auto-decider (cheap model) when enabled.
  if ($AutoDecide) {
    $ans = Invoke-DeciderClaude -Question $q -Task $TaskFile -Criteria $FrozenContract -Model $DeciderModel
    if ($ans) {
      Write-Log (New-ReviewAnswerEvent -Turn $Turn -A $ans)
      Write-Host "  [Q&A] auto-decided (model=$DeciderModel): $ans" -ForegroundColor Green
      Add-Content -Path $progressPath -Value "`n## Answer (turn $Turn, auto-decided)`nQUESTION: $q`nANSWER: $ans`nProceed accordingly; clear the QUESTION line."
      return $ans
    }
  }

  # (c) no answer available — log only; the run proceeds (the question persists).
  Write-Host "  [Q&A] no answer.json and -AutoDecide off — proceeding unanswered" -ForegroundColor DarkYellow
  return $null
}

$base       = Invoke-GateAndLog -LogEvent (-not $DryRun)
$BaseTotal  = $base.Total
$BestPass   = $base.Pass
$BestCommit = if ($UseGit) { (git rev-parse HEAD).Trim() } else { $null }
Write-Host "Baseline: $($base.Pass)/$($base.Total) pass  green=$($base.Green)  task=$TaskFile"

if ($DryRun) {
  $stageNames = ($GateStages | ForEach-Object { $_.Name }) -join ','
  Write-Host "`nDryRun OK — gate wired. git=$UseGit  bestPass=$BestPass  testHash=$($TestHash0.Substring(0,12))..."
  Write-Host "    stages=[$stageNames]  lockGlob=$LockGlob  lockedFiles=$($HashMap0.Count)  tools=$($AllowedTools.Count)  permMode=$PermissionMode  maxTurns=$MaxTurns"
  Write-Host "    models: execute=$ExecuteModel  judge=$JudgeModel  decider=$DeciderModel   verify=$([bool]$Verify)  contractCriteria=$($FrozenContract.Count)"
  $itLabel = if ($IterTimeoutSec -gt 0) { "${IterTimeoutMin}m" } else { 'off' }
  Write-Host "    resilience: iterTimeout=$itLabel  consecutiveFailLimit=$ConsecutiveFailLimit"
  Write-Host "    E5: promptCaching=on (stable-prefix)  cacheTelemetry=on  autoDecide=$([bool]$AutoDecide)  qaInbox=$StateDir/answer.json"
  Write-Host "No claude calls, no quota spent." -ForegroundColor Cyan
  exit 0
}
if ($base.Green) { Stop-Loop "already green at baseline" $true }

# --- the loop --------------------------------------------------------------
# E5 PROMPT CACHING. The prompt is structured STABLE-PREFIX-FIRST so the leading
# block (system instructions + the task spec name + the FROZEN acceptance-criteria
# contract) is byte-identical every iteration and therefore eligible for Anthropic
# prompt caching (the repeated prefix is served from cache, not re-charged at full
# input price). The only volatile per-iteration steer (the recover/verifier
# feedback) is written into .loop/progress.md, which the agent reads itself — so
# even that stays out of the cached prefix. Cache effect is observed via the
# `cache {hitRatio,warm}` event parsed from each result's `usage` (Get-CacheUsage).
$ContractBlock = if ($FrozenContract -and $FrozenContract.Count) {
  "FROZEN ACCEPTANCE CRITERIA (do not weaken; the work is done only when ALL hold):`n" +
  (($FrozenContract | ForEach-Object { "- $_" }) -join "`n")
} else {
  "FROZEN ACCEPTANCE CRITERIA: see the '## Acceptance Criteria' / '## Definition of done' section of $TaskFile."
}
$prompt = @"
You are an autonomous fix-until-green worker. Follow these stable instructions
every turn; they do not change between iterations.

$ContractBlock

PROCEDURE:
Read $TaskFile and .loop/progress.md first.

Then run ``bun test``, read the FIRST failing assertion, and make the SMALLEST
change to the implementation file named in $TaskFile that fixes it. Never edit,
skip, or delete any *.test.ts file, and do not break a test that already passes.
Re-run ``bun test`` to confirm no regression. Finally update .loop/progress.md
with what you changed, what still fails, and the next step. If you are stuck,
write ``BLOCKED: <reason>`` on the first line of .loop/progress.md and stop.
If you need a human/orchestrator decision to proceed, write
``QUESTION: <your one question>`` on the first line of .loop/progress.md and stop.
"@

# Build claude args from the parametrized tool set / permission mode.
$toolArgs = @()
foreach ($t in $AllowedTools) { $toolArgs += $t }

for ($script:Iter = 1; $script:Iter -le $MaxIters; $script:Iter++) {
  Write-Host "`n=== iteration $($script:Iter)/$MaxIters  (cum `$$([math]::Round($script:Cum,4))  best $BestPass/$BaseTotal) ===" -ForegroundColor Cyan

  # E3: cooperative safe-stop. BETWEEN iterations is the safe checkpoint for the
  # generic loop — the previous iteration already committed its work, so nothing
  # is killed mid-iteration. Honor a 'story'/'now'/'phase' STOP here.
  $script:CurStage = "iter $($script:Iter)"
  Stop-IfRequested -Scope 'story'

  # 3. EXECUTE (execute tier of the model ladder). Wrapped in a quota-survival
  # retry: a claude call that fails BECAUSE of a real usage limit triggers
  # quota-hit -> wait-and-resume -> retry the SAME iteration (no wasted iter).
  $cliArgs = @(
    '-p', $prompt, '--output-format', 'json', '--max-turns', "$MaxTurns",
    '--model', $ExecuteModel,
    '--permission-mode', $PermissionMode,
    '--allowedTools'
  ) + $toolArgs
  $res = $null
  $iterTimedOut = $false
  while ($true) {
    # E4: spawn the execute call through the isolated wrapper so a hung claude is
    # killed after $IterTimeoutSec (0 = unbounded / disabled). A timeout is a
    # NON-PRODUCTIVE iteration (no crash): emit phase-timeout and fall through.
    $exec    = Invoke-ClaudeExecute -CliArgs $cliArgs -TimeoutSec $IterTimeoutSec
    if ($exec.TimedOut) {
      Write-Log (New-PhaseTimeoutEvent -Label "iter $($script:Iter)" -TimeoutSec $IterTimeoutSec)
      Write-Host "  [TIMEOUT] iter $($script:Iter) exceeded $([int]($IterTimeoutSec/60))m — killed (hung claude)" -ForegroundColor Red
      $iterTimedOut = $true
      $res = $null
      break   # treat as non-productive: no cost, gate runs on the unchanged tree
    }
    $rawText = ("" + $exec.Raw)
    $parsed = $null
    try { $parsed = $rawText | ConvertFrom-Json } catch { $parsed = $null }
    $callFailed = ($null -eq $parsed) -or ($parsed.is_error -eq $true)
    if (-not $callFailed) { $res = $parsed; break }
    # The call looked like a failure — was it a quota limit? Probe authoritatively.
    if (Resolve-Quota "iter $($script:Iter)") { continue }   # recovered -> retry iter
    if ($null -eq $parsed) { Write-Log @{ event="parse_error"; iter=$script:Iter }; Stop-Loop "could not parse claude JSON output" $false }
    $res = $parsed; break   # a non-quota error result -> fall through with what we got
  }

  # spend accounting + 50/80/100% alerts (must happen before the verdict so the
  # cost-ceiling stop sees the updated cum)
  Update-Spend -IterCost ([double]$res.total_cost_usd)

  # E5 CACHE TELEMETRY. Emit a `cache {hitRatio,warm}` event from this iter's
  # result usage (only for a productive iter — a timed-out iter has no result).
  # Stable-prefix caching means hitRatio climbs after the first warm iteration.
  if ($null -ne $res) { Write-CacheTelemetry -Result $res | Out-Null }

  # 4. EVAL GATE + integrity signals
  $g            = Invoke-GateAndLog
  # E2 per-file tamper detection: diff the current hash-map against baseline so
  # the reason string can NAME the file that changed (not just "something").
  $hashNow      = Get-TestHashMap -LockGlob $LockGlob -BasePath $PSScriptRoot
  $tamper       = Compare-HashMap -Baseline $HashMap0 -Current $hashNow
  $tampered     = $tamper.Tampered
  $countDropped = ($g.Total -lt $BaseTotal)
  $blocked      = [bool](Select-String -Path "$StateDir/progress.md" -Pattern '^BLOCKED' -Quiet)
  $changed      = if ($UseGit) { [bool](git status --porcelain) } else { $true }

  # E5 ANSWER INBOX / Q&A SURFACE. If the agent paused with a QUESTION marker (in
  # its result body or progress.md), raise review-question, then either consume a
  # UI answer.json, auto-decide (cheap model, when -AutoDecide), or proceed. The
  # answer is fed back through progress.md for the next iteration. No-op (no event,
  # transparent) when there is no QUESTION — preserves default behavior exactly.
  $resultBody = if ($res -and $res.PSObject -and ($res.PSObject.Properties.Name -contains 'result')) { "$($res.result)" } else { '' }
  Resolve-Question -Turn $script:Iter -ResultText $resultBody | Out-Null

  # 5. VERIFY (anti-false-green). Only when -Verify AND the gate claims green AND
  # integrity is intact (no point auditing a tampered/dropped tree). A SECOND,
  # independent cheap-model judge sees ONLY the diff + frozen contract and tries
  # to refute "done". A fail suppresses the green-stop and feeds criteria back.
  $verifierRefuted = $false
  if ($Verify -and $g.Green -and -not $tampered -and -not $countDropped) {
    $diff = if ($UseGit) { (git diff HEAD 2>$null | Out-String) } else { "" }
    $judgeRaw = Invoke-JudgeClaude -Diff $diff -Criteria $FrozenContract -Model $JudgeModel
    $verdict  = ConvertFrom-VerdictJson -RawText $judgeRaw -Item $TaskFile -Model $JudgeModel
    Write-Log @{ event="verdict"; item=$verdict.item; pass=$verdict.pass;
                 failingCriteria=@($verdict.failingCriteria); evidence=$verdict.evidence;
                 nextAction=$verdict.nextAction; model=$verdict.model }
    if (-not $verdict.pass) {
      $verifierRefuted = $true
      Write-Host "  [VERIFY] REFUTED green — failing: $($verdict.failingCriteria -join '; ')" -ForegroundColor Yellow
      # Feed the failing criteria back to the next iteration's executor context.
      $fcBack = ($verdict.failingCriteria | ForEach-Object { "- $_" }) -join "`n"
      Add-Content -Path "$StateDir/progress.md" -Value "`n## Verifier refuted (iter $($script:Iter))`n$fcBack`nNext: $($verdict.nextAction)"
    } else {
      Write-Host "  [VERIFY] certified green (model=$JudgeModel)" -ForegroundColor Green
    }
  }

  # update drift counters before asking for a verdict
  if (-not $changed) { $Stale++ } else { $Stale = 0 }
  if ($changed -and $g.Pass -eq $BestPass) { $Plateau++ } elseif ($g.Pass -ne $BestPass) { $Plateau = 0; $PlateauAlerted = $false }

  # E4: PLATEAU trend-alert. The decision core already detects the plateau STOP;
  # here we emit a `plateau` event the FIRST time an episode is detected (pass
  # count flat across PlateauLimit changed iters). One-shot per episode — the
  # flag is re-armed above whenever the pass count moves off the plateau.
  if ($Plateau -ge $PlateauLimit -and -not $PlateauAlerted) {
    $PlateauAlerted = $true
    Write-Log (New-PlateauEvent -Item $script:Item -K $Plateau)
    Write-Host "  [PLATEAU] $($script:Item): pass-count flat across $Plateau changed iters" -ForegroundColor DarkYellow
  }

  $dec = Get-LoopDecision -Green $g.Green -Tampered $tampered -CountDropped $countDropped `
           -Blocked $blocked -Pass $g.Pass -BestPass $BestPass -Changed $changed `
           -RegressCount $RegressCount -RegressLimit $RegressLimit `
           -Plateau $Plateau -PlateauLimit $PlateauLimit -Stale $Stale -StagnationLimit $StagnationLimit `
           -Cum $script:Cum -Ceiling $CostCeilingUsd -Iter $script:Iter -MaxIters $MaxIters `
           -VerifierRefuted $verifierRefuted

  # Surface the named-file tamper reason in the log/handoff when it fired.
  if ($tampered) { $dec = [pscustomobject]@{ Action=$dec.Action; Green=$dec.Green; Reason=$tamper.Reason } }

  # E4: CONSECUTIVE-FAILURE -> recover-once -> handoff. A pure addition to the
  # decision inputs: count consecutive non-green iters that made NO net progress
  # (a timeout iter is one such failure). After ConsecutiveFailLimit, take ONE
  # recover action (reset-to-best hint fed back); if it STILL fails, hand off.
  # Only overrides a 'continue'/'rollback' verdict — a stronger stop (green,
  # tamper, cost, regress-handoff, stagnation, plateau, max-iters) always wins.
  $madeProgress = ($g.Pass -gt $BestPass)
  $cf = Update-ConsecutiveFail -Green $g.Green -MadeProgress $madeProgress `
          -Count $ConsecFail -Recovered $ConsecRecovered -Limit $ConsecutiveFailLimit
  $ConsecFail      = $cf.Count
  $ConsecRecovered = $cf.Recovered
  if ($dec.Action -in @('continue', 'rollback')) {
    if ($cf.Handoff) {
      Write-Log (New-HandoffEvent -Item $script:Item -Reason $cf.Reason -Consecutive $cf.Count)
      Write-Host "  [HANDOFF] $($script:Item): $($cf.Reason)" -ForegroundColor Yellow
      $dec = [pscustomobject]@{ Action='stop'; Green=$false; Reason=$cf.Reason }
    } elseif ($cf.Recover) {
      Write-Host "  [RECOVER] $($cf.Reason) — resetting to best-known-good and retrying once" -ForegroundColor DarkYellow
      if ($UseGit -and $BestCommit) { git reset --hard $BestCommit *>$null }
      Add-Content -Path "$StateDir/progress.md" -Value "`n## Recover hint (iter $($script:Iter))`nStuck after $($cf.Count) no-progress iters. Tree was reset to the best-known-good ($BestPass/$BaseTotal). Re-read $TaskFile and the FIRST failing assertion and try a DIFFERENT minimal fix."
    }
  }

  Write-Log @{ event="iter"; iter=$script:Iter; cost=[double]$res.total_cost_usd; cum=$script:Cum;
               pass=$g.Pass; total=$g.Total; best=$BestPass; changed=$changed;
               stale=$Stale; plateau=$Plateau; regress=$RegressCount; action=$dec.Action; reason=$dec.Reason }
  Write-Host ("  -> {0}/{1} pass  iter_cost=`${2}  cum=`${3}  [{4}]" -f `
              $g.Pass, $g.Total, [double]$res.total_cost_usd, [math]::Round($script:Cum,4), $dec.Action)

  # 6. ACT on the verdict
  switch ($dec.Action) {
    'stop' {
      if ($g.Pass -gt $BestPass) { $BestPass = $g.Pass }   # keep summary/log accurate on one-shot green
      # E3: a regression-handoff stop (strikes exhausted) raises the handoff
      # beacon BEFORE the stop, naming the item + how many consecutive strikes.
      if ($dec.Reason -match 'repeated regressions') {
        Write-Log (New-HandoffEvent -Item $script:Item -Reason $dec.Reason -Consecutive ($RegressCount + 1))
        Write-Host "  [HANDOFF] $($script:Item): $($dec.Reason)" -ForegroundColor Yellow
      }
      if ($dec.Green -and $UseGit -and $changed) {
        git add -A *>$null; git commit -q -m "loop $($script:Iter): GREEN $($g.Pass)/$($g.Total)" *>$null
      }
      Stop-Loop $dec.Reason $dec.Green
    }
    'rollback' {
      if ($UseGit) { git reset --hard $BestCommit *>$null }
      $RegressCount++
      # E3: surface the rollback as a strike against the budget (RegressLimit).
      Write-Log (New-RollbackEvent -Item $script:Item -ToIter $script:Iter `
                   -BestPass $BestPass -Strike $RegressCount -StrikeBudget $RegressLimit)
      Write-Host "  rollback -> best $BestPass/$BaseTotal (strike $RegressCount/$RegressLimit)" -ForegroundColor DarkYellow
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
