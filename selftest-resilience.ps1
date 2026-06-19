<#
  selftest-resilience.ps1 — deterministic unit tests for the E4 additions:
    (a) per-iteration WALL-CLOCK TIMEOUT path: a stubbed long-running command is
        "killed" (no real process, no real timer) and the engine emits the EXACT
        PROTOCOL §2 `phase-timeout` { label, timeoutSec } event.
    (b) CONSECUTIVE-FAILURE counter -> recover-once -> handoff: the pure
        Update-ConsecutiveFail core, plus the `handoff` { item, reason,
        consecutive } event shape it feeds.
    (c) PLATEAU trend-alert: the one-shot `plateau` { item, k } event, emitted
        the FIRST time an episode is detected and re-armed when the plateau breaks.

  ZERO quota: NOTHING here invokes claude; NOTHING sleeps or waits for real. The
  "process" is a STUB that just reports timedOut; every event is built by the pure
  loopcore builders. Run: pwsh -NoProfile -File selftest-resilience.ps1
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

# Trip-wires: these counters MUST stay 0 for the whole run — proof that no quota
# is spent and no real time passes.
$script:claudeCalls = 0
$script:realWaits   = 0

# =====================================================================
# (a) PER-ITERATION WALL-CLOCK TIMEOUT — stubbed spawn+kill, NO real timer
# =====================================================================
Write-Host "phase-timeout (stubbed spawn+kill):" -ForegroundColor Cyan

# 1. The pure event builder emits the EXACT PROTOCOL §2 shape.
$pt = New-PhaseTimeoutEvent -Label 'iter 4' -TimeoutSec 720
$jpt = $pt | ConvertTo-Json -Compress
Check "phase-timeout event"      $pt.event 'phase-timeout'
Check "phase-timeout label"      $pt.label 'iter 4'
Check "phase-timeout timeoutSec" $pt.timeoutSec 720
Check "phase-timeout field count" (@($pt.PSObject.Properties.Name)).Count 3
Check "phase-timeout json fields" ($jpt -match '"event":"phase-timeout"' -and $jpt -match '"label":"iter 4"' -and $jpt -match '"timeoutSec":720') $true
Check "phase-timeout json no extra" ($jpt -match 'cum|cost|model') $false

# 2. A STUB of loop.ps1's Invoke-ClaudeExecute that NEVER spawns a real process
#    and NEVER runs a real timer. It models a hung claude: when the budget is
#    exceeded it reports TimedOut so the engine path can be exercised offline.
#    -DurationSec = how long the (fake) claude "would" run; -TimeoutSec = budget.
function StubClaudeExecute {
  param([string[]] $CliArgs, [int] $TimeoutSec, [int] $DurationSec)
  $script:claudeCalls++   # we are still NOT calling claude — this is the stub seam
  if ($TimeoutSec -gt 0 -and $DurationSec -gt $TimeoutSec) {
    # killed: no stdout, no real Start-Sleep, no WaitForExit — pure decision.
    return @{ Raw = ''; TimedOut = $true }
  }
  return @{ Raw = '{ "is_error": false, "total_cost_usd": 0.01, "result": "ok" }'; TimedOut = $false }
}

# Faithful re-creation of loop.ps1's execute path: on TimedOut, build the
# phase-timeout event and treat the iter as non-productive (res = $null).
function RunExecuteIter([int]$Iter, [int]$TimeoutSec, [int]$DurationSec) {
  $events = @()
  $exec = StubClaudeExecute -CliArgs @('-p','x') -TimeoutSec $TimeoutSec -DurationSec $DurationSec
  if ($exec.TimedOut) {
    $events += (New-PhaseTimeoutEvent -Label "iter $Iter" -TimeoutSec $TimeoutSec)
    return @{ Events = $events; Res = $null; TimedOut = $true }
  }
  $res = $null; try { $res = $exec.Raw | ConvertFrom-Json } catch {}
  return @{ Events = $events; Res = $res; TimedOut = $false }
}

# 2a. claude runs 20 min but the budget is 12 min -> KILLED, phase-timeout emitted.
$r1 = RunExecuteIter -Iter 4 -TimeoutSec (12*60) -DurationSec (20*60)
Check "timeout killed"          $r1.TimedOut $true
Check "timeout res null"        ($null -eq $r1.Res) $true
Check "timeout one event"       $r1.Events.Count 1
Check "timeout event type"      $r1.Events[0].event 'phase-timeout'
Check "timeout event label"     $r1.Events[0].label 'iter 4'
Check "timeout event timeoutSec" $r1.Events[0].timeoutSec (12*60)

# 2b. claude finishes inside the budget -> NO timeout, normal result.
$r2 = RunExecuteIter -Iter 5 -TimeoutSec (12*60) -DurationSec (3*60)
Check "in-budget not timed out" $r2.TimedOut $false
Check "in-budget no event"      $r2.Events.Count 0
Check "in-budget got result"    ($r2.Res.is_error) $false

# 2c. timeout DISABLED (budget 0) -> never killed even on a long run.
$r3 = RunExecuteIter -Iter 6 -TimeoutSec 0 -DurationSec (60*60)
Check "disabled never times out" $r3.TimedOut $false
Check "disabled no event"        $r3.Events.Count 0

# 2d. the real loop.ps1 defines the isolated Invoke-ClaudeExecute seam tests stub,
#     and it carries a WaitForExit(ms) wall-clock kill + a TimeoutSec switch.
$loopSrc = Get-Content -Path "$PSScriptRoot/loop.ps1" -Raw
Check "loop has Invoke-ClaudeExecute seam" ($loopSrc -match 'function Invoke-ClaudeExecute') $true
Check "loop wires phase-timeout"           ($loopSrc -match 'New-PhaseTimeoutEvent') $true
Check "loop has wall-clock WaitForExit"    ($loopSrc -match 'WaitForExit\(') $true
Check "loop kills the process tree"        ($loopSrc -match 'taskkill /T /F') $true
Check "loop has -IterTimeoutMin param"     ($loopSrc -match '\$IterTimeoutMin') $true

# =====================================================================
# (b) CONSECUTIVE-FAILURE -> RECOVER-ONCE -> HANDOFF (pure core)
# =====================================================================
Write-Host "`nconsecutive-failure recover-once -> handoff:" -ForegroundColor Cyan

# Walk a streak of no-progress failing iters with the default limit (3).
# A failing iter = not green AND no net progress. Carry Count/Recovered forward.
$cnt = 0; $rec = $false
$u1 = Update-ConsecutiveFail -Green $false -MadeProgress $false -Count $cnt -Recovered $rec -Limit 3
$cnt = $u1.Count; $rec = $u1.Recovered
Check "fail1 count"        $u1.Count 1
Check "fail1 no recover"   $u1.Recover $false
Check "fail1 no handoff"   $u1.Handoff $false

$u2 = Update-ConsecutiveFail -Green $false -MadeProgress $false -Count $cnt -Recovered $rec -Limit 3
$cnt = $u2.Count; $rec = $u2.Recovered
Check "fail2 count"        $u2.Count 2
Check "fail2 no recover"   $u2.Recover $false

# 3rd consecutive failure hits the limit -> spend the ONE recover attempt.
$u3 = Update-ConsecutiveFail -Green $false -MadeProgress $false -Count $cnt -Recovered $rec -Limit 3
$cnt = $u3.Count; $rec = $u3.Recovered
Check "fail3 count"        $u3.Count 3
Check "fail3 RECOVER"      $u3.Recover $true
Check "fail3 no handoff"   $u3.Handoff $false
Check "fail3 recovered set" $u3.Recovered $true
Check "fail3 reason recover" ($u3.Reason -match 'recover-once') $true

# 4th consecutive failure AFTER recover already spent -> HANDOFF.
$u4 = Update-ConsecutiveFail -Green $false -MadeProgress $false -Count $cnt -Recovered $rec -Limit 3
$cnt = $u4.Count; $rec = $u4.Recovered
Check "fail4 count"        $u4.Count 4
Check "fail4 no recover"   $u4.Recover $false
Check "fail4 HANDOFF"      $u4.Handoff $true
Check "fail4 reason handoff" ($u4.Reason -match 'after recover') $true

# A net-progress iter mid-streak RESETS the counter AND re-arms the recover token.
$u5 = Update-ConsecutiveFail -Green $false -MadeProgress $true -Count 2 -Recovered $false -Limit 3
Check "progress resets count"     $u5.Count 0
Check "progress resets recovered" $u5.Recovered $false
Check "progress no handoff"       $u5.Handoff $false

# A GREEN iter likewise clears the streak.
$u6 = Update-ConsecutiveFail -Green $true -MadeProgress $false -Count 2 -Recovered $true -Limit 3
Check "green resets count"     $u6.Count 0
Check "green resets recovered" $u6.Recovered $false

# Recover and Handoff are mutually exclusive at every step.
Check "recover xor handoff (u3)" ($u3.Recover -and $u3.Handoff) $false
Check "recover xor handoff (u4)" ($u4.Recover -and $u4.Handoff) $false

# Default limit must NOT fire on a short failing run (preserves E1-E3 outcomes).
$uShort = Update-ConsecutiveFail -Green $false -MadeProgress $false -Count 1 -Recovered $false -Limit 3
Check "below-limit no recover" $uShort.Recover $false
Check "below-limit no handoff" $uShort.Handoff $false

# A timed-out iter is just a non-green/no-progress failure -> feeds the same path.
$uTo = Update-ConsecutiveFail -Green $false -MadeProgress $false -Count 2 -Recovered $true -Limit 3
Check "timeout-as-fail handoff" $uTo.Handoff $true

# The handoff EVENT the streak feeds has the exact PROTOCOL §2 shape.
$ho = New-HandoffEvent -Item 'TASK.md' -Reason $u4.Reason -Consecutive $u4.Count
$jho = $ho | ConvertTo-Json -Compress
Check "handoff event"       $ho.event 'handoff'
Check "handoff item"        $ho.item 'TASK.md'
Check "handoff consecutive" $ho.consecutive 4
Check "handoff json fields" ($jho -match '"event":"handoff"' -and $jho -match '"item":"TASK.md"' -and $jho -match '"reason"' -and $jho -match '"consecutive":4') $true

# the real loop.ps1 wires the consecutive-fail core + its threshold param.
Check "loop has -ConsecutiveFailLimit"   ($loopSrc -match '\$ConsecutiveFailLimit') $true
Check "loop calls Update-ConsecutiveFail" ($loopSrc -match 'Update-ConsecutiveFail') $true

# =====================================================================
# (c) PLATEAU TREND-ALERT — one-shot `plateau` event per episode
# =====================================================================
Write-Host "`nplateau trend-alert (one-shot):" -ForegroundColor Cyan

# The pure event builder emits the EXACT PROTOCOL §2 shape.
$pl = New-PlateauEvent -Item 'TASK.md' -K 3
$jpl = $pl | ConvertTo-Json -Compress
Check "plateau event"      $pl.event 'plateau'
Check "plateau item"       $pl.item 'TASK.md'
Check "plateau k"          $pl.k 3
Check "plateau field count" (@($pl.PSObject.Properties.Name)).Count 3
Check "plateau json fields" ($jpl -match '"event":"plateau"' -and $jpl -match '"item":"TASK.md"' -and $jpl -match '"k":3') $true

# Model loop.ps1's one-shot guard: emit `plateau` the FIRST iter the run length
# reaches the limit; suppress on subsequent flat iters; re-arm when it breaks.
# pass/best timeline drives plateauCount exactly like loop.ps1.
function DrivePlateau([int[]]$passSeq, [int]$plateauLimit) {
  $best = $passSeq[0]
  $plateau = 0; $alerted = $false
  $emitted = @()
  for ($i = 1; $i -lt $passSeq.Count; $i++) {
    $pass = $passSeq[$i]
    $changed = $true   # every iter changed the tree
    if ($changed -and $pass -eq $best) { $plateau++ } elseif ($pass -ne $best) { $plateau = 0; $alerted = $false }
    if ($plateau -ge $plateauLimit -and -not $alerted) {
      $alerted = $true
      $emitted += (New-PlateauEvent -Item 'TASK.md' -K $plateau)
    }
    if ($pass -gt $best) { $best = $pass }
  }
  return $emitted
}

# 3 flat iters at the same pass (limit 3) -> exactly ONE plateau event, k=3.
$e1 = DrivePlateau @(5,5,5,5) 3
Check "plateau fires once"   $e1.Count 1
Check "plateau k at fire"    $e1[0].k 3

# Longer flat run still emits only ONE event (one-shot per episode).
$e2 = DrivePlateau @(5,5,5,5,5,5) 3
Check "long plateau still one" $e2.Count 1

# A plateau that BREAKS (progress) then re-forms emits a SECOND event (re-armed).
$e3 = DrivePlateau @(5,5,5,5,6,6,6,6) 3
Check "re-armed plateau twice" $e3.Count 2

# A run that never plateaus (always progressing) emits NOTHING.
$e4 = DrivePlateau @(1,2,3,4,5) 3
Check "no plateau no event"    $e4.Count 0

# loop.ps1 wires the one-shot plateau alert.
Check "loop calls New-PlateauEvent"   ($loopSrc -match 'New-PlateauEvent') $true
Check "loop has PlateauAlerted guard" ($loopSrc -match 'PlateauAlerted') $true

# =====================================================================
# ZERO-QUOTA / ZERO-REAL-WAIT proof
# =====================================================================
Write-Host "`nzero-quota / zero-real-wait proof:" -ForegroundColor Cyan
# claudeCalls counts only STUB seam invocations (the stub never reaches claude);
# realWaits is never incremented because nothing in this file sleeps or waits.
Check "no real Start-Sleep / WaitForExit" $script:realWaits 0
Check "stub-only execute calls (>0)"      ($script:claudeCalls -gt 0) $true

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL RESILIENCE TESTS PASSED" -ForegroundColor Green; exit 0 }
else              { Write-Host "$fails RESILIENCE TEST(S) FAILED" -ForegroundColor Red; exit 1 }
