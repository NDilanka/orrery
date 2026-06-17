<#
.SYNOPSIS
  Minimal self-prompting loop: generate -> test -> fix until green.
  A hand-rolled harness around `claude -p` (headless). Windows / PowerShell /
  Bun / Claude Max-aware. See loop-engineering.md for the design rationale.

.DESCRIPTION
  The ORCHESTRATOR owns everything the model cannot be trusted with:
    - cumulative cost accounting   (--max-budget-usd only bounds ONE call)
    - the eval gate                (we run `bun test`, not the model's word)
    - test-tamper detection        (SHA-256 over *.test.ts each iteration)
    - stagnation / drift detection (no diff, or pass-count regression)
    - state + rollback             (.loop/ files + per-iteration git commit)
    - stop conditions + handoff

.PARAMETER DryRun
  Validate wiring WITHOUT calling claude or spending quota: checks git, hashes
  the tests, runs `bun test` once, prints the parsed gate result, and exits.

.EXAMPLE
  pwsh -File loop.ps1 -DryRun
  pwsh -File loop.ps1 -MaxIters 15 -CostCeilingUsd 3.00
#>
[CmdletBinding()]
param(
  [int]    $MaxIters        = 15,      # hard backstop on iterations
  [double] $CostCeilingUsd  = 3.00,    # cumulative USD across ALL iterations
  [int]    $MaxTurns        = 30,      # turns within a single claude -p call
  [int]    $StagnationLimit = 2,       # consecutive no-diff iters -> handoff
  [string] $StateDir        = ".loop",
  [switch] $DryRun
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# --- helpers ---------------------------------------------------------------

function Get-TestHash {
  # Authoritative anti-cheat: hash of all test files, order-stable.
  # If a test is edited, skipped, or deleted, this string changes.
  $files = Get-ChildItem -Recurse -Filter *.test.ts | Sort-Object FullName
  if (-not $files) { return "" }
  ($files | Get-FileHash -Algorithm SHA256 | ForEach-Object Hash) -join ""
}

function Invoke-Gate {
  # The EVAL GATE. Runs the real tests; returns pass/fail counts + green-ness.
  $output    = & bun test 2>&1 | Out-String
  $exit      = $LASTEXITCODE
  $pass      = if ($output -match '(\d+)\s+pass') { [int]$Matches[1] } else { 0 }
  $fail      = if ($output -match '(\d+)\s+fail') { [int]$Matches[1] } else { 0 }
  [pscustomobject]@{
    Green = ($exit -eq 0)
    Pass  = $pass
    Fail  = $fail
    Total = $pass + $fail
    Raw   = $output
  }
}

function Write-Log($obj) {
  ($obj | ConvertTo-Json -Compress) | Add-Content -Path "$StateDir/log.jsonl"
}

function Stop-Loop($reason, $green) {
  $tag = if ($green) { "[OK]" } else { "[HANDOFF]" }
  Write-Host ""
  Write-Host "$tag $reason" -ForegroundColor ($(if ($green) {"Green"} else {"Yellow"}))
  Write-Host "    iters=$script:Iter  cumulative=`$$([math]::Round($script:Cum,4))"
  Write-Log @{ event="stop"; reason=$reason; green=$green; iter=$script:Iter; cum=$script:Cum }
  exit ($(if ($green) { 0 } else { 1 }))
}

# --- preflight -------------------------------------------------------------

if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }

$UseGit = $false
try { if ((git rev-parse --is-inside-work-tree 2>$null) -eq 'true') { $UseGit = $true } } catch {}

$TestHash0 = Get-TestHash
if (-not $TestHash0) { throw "No *.test.ts files found — nothing to gate against." }

$script:Iter = 0
$script:Cum  = 0.0
$Stale       = 0

# Baseline gate read (also the DryRun deliverable).
$base = Invoke-Gate
Write-Host "Baseline: $($base.Pass) pass / $($base.Fail) fail  green=$($base.Green)  tests=$($base.Total)"
$BaseTotal = $base.Total

if ($DryRun) {
  Write-Host ""
  Write-Host "DryRun OK — gate wired correctly. git=$UseGit  testHash=$($TestHash0.Substring(0,12))..."
  Write-Host "No claude calls made, no quota spent." -ForegroundColor Cyan
  exit 0
}
if ($base.Green) { Stop-Loop "already green at baseline" $true }

# --- the loop --------------------------------------------------------------

$prompt = @'
Read TASK.md and .loop/progress.md first.

Then run `bun test`, read the FIRST failing assertion, and make the SMALLEST
change to src/roman.ts that fixes it. Never edit, skip, or delete any test file.
Re-run `bun test` to confirm you did not regress. Finally, update
.loop/progress.md with: what you changed, what still fails, and the next step.
If you are stuck, write `BLOCKED: <reason>` on the first line of
.loop/progress.md and stop.
'@

for ($script:Iter = 1; $script:Iter -le $MaxIters; $script:Iter++) {
  Write-Host "`n=== iteration $script:Iter / $MaxIters  (cum `$$([math]::Round($script:Cum,4))) ===" -ForegroundColor Cyan

  # 3. EXECUTE — one headless agent turn (ReAct happens inside it).
  $cliArgs = @(
    '-p', $prompt,
    '--output-format', 'json',
    '--max-turns', "$MaxTurns",
    '--permission-mode', 'acceptEdits',
    '--allowedTools', 'Read', 'Edit', 'Write', 'Bash(bun test)', 'Bash(bun test:*)'
  )
  $rawText = & claude @cliArgs 2>$null | Out-String
  try   { $res = $rawText | ConvertFrom-Json }
  catch { Write-Log @{ event="parse_error"; iter=$script:Iter }; Stop-Loop "could not parse claude JSON output" $false }

  if ($res.total_cost_usd) { $script:Cum += [double]$res.total_cost_usd }

  # 4. EVAL GATE — orchestrator runs the tests itself.
  $g = Invoke-Gate

  # 4a. Anti-cheat: tests must be byte-for-byte unchanged.
  if ((Get-TestHash) -ne $TestHash0) { Stop-Loop "test files were modified" $false }
  # 4b. Anti-cheat: test count must not shrink (deleted/skipped cases).
  if ($g.Total -lt $BaseTotal)       { Stop-Loop "test count dropped ($($g.Total) < $BaseTotal)" $false }

  # 5. PERSIST — log + commit productive iterations for rollback.
  $changed = if ($UseGit) { [bool](git status --porcelain) } else { $true }
  if ($UseGit -and $changed) {
    git add -A 2>$null | Out-Null
    git commit -q -m "loop iter $script:Iter: $($g.Pass)/$($g.Total) pass" 2>$null | Out-Null
  }
  Write-Log @{ event="iter"; iter=$script:Iter; cost=[double]$res.total_cost_usd; cum=$script:Cum;
               green=$g.Green; pass=$g.Pass; fail=$g.Fail; total=$g.Total; changed=$changed }
  Write-Host "  -> $($g.Pass)/$($g.Total) pass  iter_cost=`$$([double]$res.total_cost_usd)  cum=`$$([math]::Round($script:Cum,4))"

  # 6. DECIDE next — ordered stop conditions.
  if ($g.Green)                                              { Stop-Loop "all tests green at iter $script:Iter" $true }
  if ($script:Cum -ge $CostCeilingUsd)                      { Stop-Loop "cost ceiling `$$CostCeilingUsd reached" $false }
  if (Select-String -Path "$StateDir/progress.md" -Pattern '^BLOCKED' -Quiet) { Stop-Loop "agent reported BLOCKED" $false }
  if (-not $changed) { $Stale++ } else { $Stale = 0 }
  if ($Stale -ge $StagnationLimit)                          { Stop-Loop "stagnation: $Stale iters with no change" $false }
}

Stop-Loop "max iterations ($MaxIters) reached without green" $false
