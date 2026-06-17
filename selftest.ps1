<#
  selftest.ps1 — deterministic unit tests for the harness DECISION logic.
  Runs the evaluation-hardening branches through synthetic state. NO claude
  calls, NO quota. This is how we trust the guardrails without waiting for the
  model to misbehave on cue.

  Run: pwsh -NoProfile -File selftest.ps1
#>
. "$PSScriptRoot/loopcore.ps1"

$fails = 0
function Check($name, $got, $want) {
  $ok = ($got -eq $want)
  $tag = if ($ok) { "PASS" } else { "FAIL" }
  $col = if ($ok) { "Green" } else { "Red" }
  Write-Host ("  [{0}] {1}  (got '{2}', want '{3}')" -f $tag, $name, $got, $want) -ForegroundColor $col
  if (-not $ok) { $script:fails++ }
}

# Sensible defaults; each scenario overrides only what it tests.
function D($over) {
  $p = @{
    Green=$false; Tampered=$false; CountDropped=$false; Blocked=$false;
    Pass=5; BestPass=5; Changed=$true;
    RegressCount=0; RegressLimit=3; Plateau=0; PlateauLimit=3;
    Stale=0; StagnationLimit=2; Cum=0.0; Ceiling=3.0; Iter=2; MaxIters=15
  }
  foreach ($k in $over.Keys) { $p[$k] = $over[$k] }
  Get-LoopDecision @p
}

Write-Host "Decision-logic self-test:`n"

Check "green wins"                 (D @{ Green=$true }).Action                                  "stop"
Check "green is success"           (D @{ Green=$true }).Green                                   $true
Check "tamper beats green"         (D @{ Green=$true; Tampered=$true }).Reason.Substring(0,4)   "test"
Check "tamper handoff"             (D @{ Tampered=$true }).Green                                 $false
Check "count drop handoff"         (D @{ CountDropped=$true }).Action                            "stop"
Check "blocked handoff"            (D @{ Blocked=$true }).Action                                 "stop"
Check "cost ceiling beats regress" (D @{ Cum=3.0; Pass=1; BestPass=5 }).Reason.Substring(0,4)    "cost"
Check "regression -> rollback"     (D @{ Pass=3; BestPass=5 }).Action                            "rollback"
Check "regress at limit -> stop"   (D @{ Pass=3; BestPass=5; RegressCount=2; RegressLimit=3 }).Action "stop"
Check "stagnation (no change)"     (D @{ Changed=$false; Stale=2; StagnationLimit=2 }).Action    "stop"
Check "no stop below stale limit"  (D @{ Changed=$false; Stale=1; StagnationLimit=2 }).Action    "continue"
Check "plateau (churn, no gain)"   (D @{ Changed=$true; Pass=5; BestPass=5; Plateau=3; PlateauLimit=3 }).Action "stop"
Check "below plateau -> continue"  (D @{ Changed=$true; Pass=5; BestPass=5; Plateau=1 }).Action  "continue"
Check "max iters -> stop"          (D @{ Iter=15; MaxIters=15 }).Action                          "stop"
Check "normal progress continues"  (D @{ Pass=6; BestPass=5 }).Action                            "continue"

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL DECISION TESTS PASSED" -ForegroundColor Green; exit 0 }
else              { Write-Host "$fails DECISION TEST(S) FAILED" -ForegroundColor Red; exit 1 }
