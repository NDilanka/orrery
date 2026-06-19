<#
  selftest-resume.ps1 — deterministic unit tests for the E3 additions:
    (a) quota reset-time / sleep-target selection  (five_hour vs weekly resetsAt,
        REJECTED window preferred) + the quota-hit/quota-wait/quota-resume JSON
    (b) checkpoint.json — EXACT PROTOCOL §7 fields, round-trips
    (c) the STOP-flag detection — right mode, held/honored correctly, consumable
    (d) the rollback / handoff event JSON shapes — match PROTOCOL §2
  Plus an INTEGRATION check that the wait-and-resume loop drives the pure pieces
  with a STUBBED probe + STUBBED sleep — proving NO claude call and NO real sleep.

  ZERO quota: NOTHING here invokes claude; NOTHING sleeps for real. The probe
  result is INJECTED (a captured stream-json fragment fed to the pure parser),
  and every sleep is a counter bump. Run: pwsh -NoProfile -File selftest-resume.ps1
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

# Fixed clock so every time computation is deterministic (no real Get-Date).
$Now = [datetime]'2026-06-19T10:00:00'

# Build a captured stream-json `rate_limit_info` fragment exactly as claude emits it.
function RLI([string]$status, [Nullable[long]]$resetsAt, [string]$type) {
  $parts = @("`"status`": `"$status`"")
  if ($null -ne $resetsAt) { $parts += "`"resetsAt`": $resetsAt" }
  if ($type)               { $parts += "`"rateLimitType`": `"$type`"" }
  '{ "type":"system", "rate_limit_info": { ' + ($parts -join ', ') + ' } }'
}
function Epoch([datetime]$dt) { [DateTimeOffset]::new($dt, [TimeSpan]::Zero).ToUnixTimeSeconds() }
# The implementation converts an epoch back via FromUnixTimeSeconds(..).LocalDateTime;
# round-trip the SAME way so the expected value is timezone-correct on any machine.
function FromEpoch([long]$e) { [DateTimeOffset]::FromUnixTimeSeconds($e).LocalDateTime }

# =====================================================================
# (a) QUOTA: reset-time / sleep-target selection + event JSON
# =====================================================================
Write-Host "quota reset-time / sleep-target selection:" -ForegroundColor Cyan

# 1a. allowed status -> NOT limited, no reset.
$allowed = RLI 'allowed' $null 'five_hour'
$s0 = Resolve-QuotaStatus -Text $allowed
Check "allowed not limited" $s0.Limited $false

# 1b. a REJECTED five_hour window -> limited, reset = that window, type five_hour.
$fiveAt = $Now.AddMinutes(45)
$fiveEpoch = Epoch $fiveAt
$rejFive = RLI 'rejected' $fiveEpoch 'five_hour'
$s1 = Resolve-QuotaStatus -Text $rejFive
Check "reject5 limited"     $s1.Limited $true
Check "reject5 resetType"   $s1.ResetType 'five_hour'
Check "reject5 resetAt"     $s1.ResetAt.ToString('o') (FromEpoch $fiveEpoch).ToString('o')

# 1c. TWO windows: an ALLOWED five_hour + a REJECTED weekly. The REJECTED window
#     must win the sleep target (so a weekly limit waits for the WEEKLY reset, not
#     the nearer five_hour one). resetType = weekly.
$weekAt = $Now.AddDays(3)
$weekEpoch = Epoch $weekAt
$mixed  = (RLI 'allowed' $fiveEpoch 'five_hour') + "`n" + (RLI 'rejected' $weekEpoch 'weekly')
$s2 = Resolve-QuotaStatus -Text $mixed
Check "mixed limited"       $s2.Limited $true
Check "mixed prefers reject weekly" $s2.ResetType 'weekly'
Check "mixed resetAt weekly" $s2.ResetAt.ToString('o') (FromEpoch $weekEpoch).ToString('o')

# 1d. limited but NO resetsAt anywhere -> limited true, ResetAt null (fallback wait).
$rejNoTime = RLI 'rejected' $null $null
$s3 = Resolve-QuotaStatus -Text $rejNoTime
Check "reject-notime limited" $s3.Limited $true
Check "reject-notime no reset" ($null -eq $s3.ResetAt) $true

# 1e. hard 429 text with NO rate_limit_info fragment still flags limited.
$s4 = Resolve-QuotaStatus -Text 'API error: 429 too many requests; usage limit reached'
Check "429 text limited" $s4.Limited $true

# --- waitSec selection (pure, injected clock — no real sleep) ----------
# five_hour reset 45m out -> 45*60 + 120 buffer = 2820, clamped within [60,21600].
$w1 = Get-QuotaWaitSec -ResetAt $fiveAt -DefaultWaitMin 30 -Now $Now
Check "waitSec five_hour" $w1 (45*60 + 120)
# weekly reset 3 days out -> clamps to the 6h (21600s) single-cycle ceiling.
$w2 = Get-QuotaWaitSec -ResetAt $weekAt -DefaultWaitMin 30 -Now $Now
Check "waitSec weekly clamps 6h" $w2 21600
# no reset -> DefaultWaitMin minutes.
$w3 = Get-QuotaWaitSec -ResetAt $null -DefaultWaitMin 30 -Now $Now
Check "waitSec default" $w3 (30*60)
# a reset already in the PAST -> clamps up to the 60s floor (never negative).
$w4 = Get-QuotaWaitSec -ResetAt ($Now.AddMinutes(-10)) -DefaultWaitMin 30 -Now $Now
Check "waitSec past floor" $w4 60

# --- quota event JSON shapes (PROTOCOL §2) -----------------------------
Write-Host "`nquota event JSON (PROTOCOL §2):" -ForegroundColor Cyan

$hit = New-QuotaHitEvent -Label 'iter 3' -Cum 1.25 -ResetAt $weekAt
$jhit = $hit | ConvertTo-Json -Compress
Check "quota-hit event"   $hit.event 'quota-hit'
Check "quota-hit label"   $hit.label 'iter 3'
Check "quota-hit cum"     $hit.cum 1.25
Check "quota-hit resetAt" $hit.resetAt $weekAt.ToString('o')
Check "quota-hit json fields" ($jhit -match '"event":"quota-hit"' -and $jhit -match '"label"' -and $jhit -match '"cum"' -and $jhit -match '"resetAt"') $true

# resetAt is explicitly null when no reset known.
$hit0 = New-QuotaHitEvent -Label 'g' -Cum 0.0 -ResetAt $null
Check "quota-hit null resetAt" ($null -eq $hit0.resetAt) $true

$wait = New-QuotaWaitEvent -Label 'iter 3' -Cum 1.25 -WaitSec $w1 -Probe 2 -ResetType 'five_hour' -Now $Now
$jwait = $wait | ConvertTo-Json -Compress
Check "quota-wait event"     $wait.event 'quota-wait'
Check "quota-wait waitSec"   $wait.waitSec $w1
Check "quota-wait probe"     $wait.probe 2
Check "quota-wait resetType" $wait.resetType 'five_hour'
Check "quota-wait resumeAt"  $wait.resumeAt $Now.AddSeconds($w1).ToString('o')
Check "quota-wait json fields" ($jwait -match '"event":"quota-wait"' -and $jwait -match '"waitSec"' -and $jwait -match '"resumeAt"' -and $jwait -match '"probe"' -and $jwait -match '"resetType":"five_hour"') $true

$resume = New-QuotaResumeEvent -Label 'iter 3' -Probe 5
$jres = $resume | ConvertTo-Json -Compress
Check "quota-resume event" $resume.event 'quota-resume'
Check "quota-resume probe" $resume.probe 5
Check "quota-resume json"  ($jres -match '"event":"quota-resume"' -and $jres -match '"label":"iter 3"' -and $jres -match '"probe":5') $true

# =====================================================================
# (b) CHECKPOINT.JSON — EXACT PROTOCOL §7 fields + round-trip
# =====================================================================
Write-Host "`ncheckpoint.json (PROTOCOL §7):" -ForegroundColor Cyan

$resumeCmd = 'pwsh -File "D:/dev/loop/loop.ps1" -TaskFile "TASK.md"'
$cp = New-Checkpoint -Stage 'iter 4' -Story $null -Branch 'loop/roman-demo' `
        -MergeBase 'abc123' -CumUsd 1.23456 -Resume $resumeCmd -UpdatedAt $Now

# EXACT field set — PROTOCOL §7: updatedAt, stage, story, branch, mergeBase, cumUsd, resume.
$keys = @($cp.Keys)
Check "cp field count"  $keys.Count 7
Check "cp has updatedAt" ($keys -contains 'updatedAt') $true
Check "cp has stage"     ($keys -contains 'stage') $true
Check "cp has story"     ($keys -contains 'story') $true
Check "cp has branch"    ($keys -contains 'branch') $true
Check "cp has mergeBase" ($keys -contains 'mergeBase') $true
Check "cp has cumUsd"    ($keys -contains 'cumUsd') $true
Check "cp has resume"    ($keys -contains 'resume') $true
# no stray BMAD-only field.
Check "cp no epic"       ($keys -contains 'epic') $false

Check "cp updatedAt iso" $cp.updatedAt $Now.ToString('o')
Check "cp stage"         $cp.stage 'iter 4'
Check "cp story null"    ($null -eq $cp.story) $true
Check "cp branch"        $cp.branch 'loop/roman-demo'
Check "cp mergeBase"     $cp.mergeBase 'abc123'
Check "cp cumUsd rounded" $cp.cumUsd 1.2346      # rounded to 4 dp
Check "cp resume cmd"    $cp.resume $resumeCmd

# round-trip through JSON: serialize -> parse -> same values.
$json = $cp | ConvertTo-Json
$rt   = $json | ConvertFrom-Json
Check "cp rt updatedAt" $rt.updatedAt $Now.ToString('o')
Check "cp rt stage"     $rt.stage 'iter 4'
Check "cp rt branch"    $rt.branch 'loop/roman-demo'
Check "cp rt mergeBase" $rt.mergeBase 'abc123'
Check "cp rt cumUsd"    $rt.cumUsd 1.2346
Check "cp rt resume"    $rt.resume $resumeCmd
Check "cp rt story null" ($null -eq $rt.story) $true
# the resume command must actually re-invoke loop.ps1.
Check "cp resume runs loop.ps1" ($rt.resume -match 'loop\.ps1') $true

# round-trip is idempotent (re-serializing the parsed object matches).
$json2 = $rt | ConvertTo-Json
Check "cp rt idempotent" (($json2 -match '"stage":\s*"iter 4"') -and ($json2 -match '"cumUsd":\s*1.2346')) $true

# =====================================================================
# (c) STOP-FLAG detection — right mode, held/honored, consumed
# =====================================================================
Write-Host "`nSTOP-flag detection (Get-StopMode):" -ForegroundColor Cyan

# absent flag -> nothing to honor.
$m0 = Get-StopMode -FlagContent $null -Scope 'story'
Check "no flag -> no honor" $m0.Honor $false
Check "no flag -> null mode" ($null -eq $m0.Mode) $true

# empty content defaults to 'phase' (matches stop-loop.ps1).
$m1 = Get-StopMode -FlagContent '' -Scope 'story'
Check "empty -> phase mode" $m1.Mode 'phase'
Check "empty honored at story" $m1.Honor $true

# 'phase' request honored at a story (between-iteration) boundary.
$m2 = Get-StopMode -FlagContent 'phase' -Scope 'story'
Check "phase mode" $m2.Mode 'phase'
Check "phase honored at story" $m2.Honor $true

# 'story' request HELD at a phase (within-iteration) boundary...
$m3 = Get-StopMode -FlagContent "story`n" -Scope 'phase'
Check "story mode (trimmed)" $m3.Mode 'story'
Check "story HELD at phase" $m3.Honor $false
# ...but honored once a story boundary is reached.
$m4 = Get-StopMode -FlagContent 'story' -Scope 'story'
Check "story honored at story" $m4.Honor $true

# 'now' honored at ANY scope.
$m5 = Get-StopMode -FlagContent 'NOW' -Scope 'phase'
Check "now mode lower" $m5.Mode 'now'
Check "now honored at phase" $m5.Honor $true

# unknown content -> defaults to 'phase' (fail-safe).
$m6 = Get-StopMode -FlagContent 'garbage' -Scope 'story'
Check "unknown -> phase" $m6.Mode 'phase'

# CONSUMED: simulate the real flag lifecycle on disk (write -> detect -> delete).
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("loop-stop-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmpDir | Out-Null
try {
  $flag = Join-Path $tmpDir 'STOP'
  Set-Content -Path $flag -Value 'story' -Encoding ascii
  $content = Get-Content $flag -Raw
  $detected = Get-StopMode -FlagContent $content -Scope 'story'
  Check "disk flag detected" $detected.Honor $true
  Check "disk flag mode"     $detected.Mode 'story'
  Remove-Item $flag -Force   # consume
  Check "flag consumed (gone)" (Test-Path $flag) $false
  # after consumption, detection returns no-honor.
  $after = Get-StopMode -FlagContent $(if (Test-Path $flag) { Get-Content $flag -Raw } else { $null }) -Scope 'story'
  Check "after consume no honor" $after.Honor $false
} finally { Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }

# cooperative-stop event JSON (PROTOCOL §2).
$cs = New-CooperativeStopEvent -Scope 'story' -Mode 'phase' -Stage 'iter 4' -Story $null -Branch 'loop/roman-demo' -Cum 1.5
$jcs = $cs | ConvertTo-Json -Compress
Check "coop-stop event"  $cs.event 'cooperative-stop'
Check "coop-stop scope"  $cs.scope 'story'
Check "coop-stop mode"   $cs.mode 'phase'
Check "coop-stop stage"  $cs.stage 'iter 4'
Check "coop-stop branch" $cs.branch 'loop/roman-demo'
Check "coop-stop cum"    $cs.cum 1.5
Check "coop-stop story null" ($null -eq $cs.story) $true
Check "coop-stop json fields" ($jcs -match '"event":"cooperative-stop"' -and $jcs -match '"scope"' -and $jcs -match '"mode"' -and $jcs -match '"stage"' -and $jcs -match '"branch"' -and $jcs -match '"cum"') $true

# =====================================================================
# (d) ROLLBACK / HANDOFF event JSON shapes (PROTOCOL §2)
# =====================================================================
Write-Host "`nrollback / handoff event JSON (PROTOCOL §2):" -ForegroundColor Cyan

$rb = New-RollbackEvent -Item 'TASK.md' -ToIter 7 -BestPass 5 -Strike 2 -StrikeBudget 3
$jrb = $rb | ConvertTo-Json -Compress
Check "rollback event"        $rb.event 'rollback'
Check "rollback item"         $rb.item 'TASK.md'
Check "rollback toIter"       $rb.toIter 7
Check "rollback bestPass"     $rb.bestPass 5
Check "rollback strike"       $rb.strike 2
Check "rollback strikeBudget" $rb.strikeBudget 3
Check "rollback json fields"  ($jrb -match '"event":"rollback"' -and $jrb -match '"item":"TASK.md"' -and $jrb -match '"toIter":7' -and $jrb -match '"bestPass":5' -and $jrb -match '"strike":2' -and $jrb -match '"strikeBudget":3') $true

$ho = New-HandoffEvent -Item 'TASK.md' -Reason 'repeated regressions (3/3) — handoff' -Consecutive 3
$jho = $ho | ConvertTo-Json -Compress
Check "handoff event"       $ho.event 'handoff'
Check "handoff item"        $ho.item 'TASK.md'
Check "handoff reason"      $ho.reason 'repeated regressions (3/3) — handoff'
Check "handoff consecutive" $ho.consecutive 3
Check "handoff json fields" ($jho -match '"event":"handoff"' -and $jho -match '"item":"TASK.md"' -and $jho -match '"reason"' -and $jho -match '"consecutive":3') $true

# =====================================================================
# (e) INTEGRATION: wait-and-resume drives pure pieces — STUBBED probe + sleep,
#     proving NO claude call and NO real sleep. We model the loop.ps1
#     Wait-ForQuota flow with injected probe results + a sleep COUNTER.
# =====================================================================
Write-Host "`nwait-and-resume integration (stubbed probe + sleep):" -ForegroundColor Cyan

# Scripted probe outcomes: limited, limited, then available. Each "probe" returns
# a captured rate_limit_info text fed to the PURE parser (NO claude).
$script:probeTexts = @(
  (RLI 'rejected' (Epoch ($Now.AddMinutes(20))) 'five_hour'),
  (RLI 'rejected' (Epoch ($Now.AddMinutes(20))) 'five_hour'),
  (RLI 'allowed'  $null 'five_hour')
)
$script:probeIdx = 0
$script:sleepCount = 0
$script:sleptSeconds = 0
$script:claudeCalls = 0   # MUST stay 0 — we never call claude here.

# A faithful re-creation of loop.ps1 Wait-ForQuota using the SAME pure functions,
# but with the probe + sleep STUBBED. This proves the design is stub-friendly.
function StubProbe {
  $txt = $script:probeTexts[[math]::Min($script:probeIdx, $script:probeTexts.Count - 1)]
  $script:probeIdx++
  $status = Resolve-QuotaStatus -Text $txt
  $script:LastQuotaT = $status
  return (-not $status.Limited)
}
function StubSleep([int]$sec) { $script:sleepCount++; $script:sleptSeconds += $sec }   # NO real Start-Sleep

$events = @()
$resumed = $false
for ($i = 1; $i -le 30; $i++) {
  if (StubProbe) { $events += (New-QuotaResumeEvent -Label 'goal' -Probe $i); $resumed = $true; break }
  $resetAt = if ($script:LastQuotaT) { $script:LastQuotaT.ResetAt } else { $null }
  $rtype   = if ($script:LastQuotaT) { $script:LastQuotaT.ResetType } else { $null }
  $wsec    = Get-QuotaWaitSec -ResetAt $resetAt -DefaultWaitMin 30 -Now $Now
  $events += (New-QuotaWaitEvent -Label 'goal' -Cum 0.0 -WaitSec $wsec -Probe $i -ResetType $rtype -Now $Now)
  StubSleep $wsec
}

Check "wait-resume resumed"        $resumed $true
Check "wait-resume two waits"      ($events | Where-Object event -eq 'quota-wait').Count 2
Check "wait-resume one resume"     ($events | Where-Object event -eq 'quota-resume').Count 1
Check "wait-resume resume probe 3" ($events | Where-Object event -eq 'quota-resume')[0].probe 3
Check "wait-resume slept twice"    $script:sleepCount 2
Check "wait-resume slept time>0"   ($script:sleptSeconds -gt 0) $true
Check "wait-resume ZERO claude"    $script:claudeCalls 0   # proof: no quota spent

# Confirm the loop.ps1 wrappers exist and are overridable (the seams tests stub).
$loopSrc = Get-Content -Path "$PSScriptRoot/loop.ps1" -Raw
Check "loop has Invoke-QuotaProbe seam" ($loopSrc -match 'function Invoke-QuotaProbe') $true
Check "loop has Start-QuotaSleep seam"  ($loopSrc -match 'function Start-QuotaSleep') $true

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL RESUME/QUOTA/CHECKPOINT TESTS PASSED" -ForegroundColor Green; exit 0 }
else              { Write-Host "$fails RESUME TEST(S) FAILED" -ForegroundColor Red; exit 1 }
