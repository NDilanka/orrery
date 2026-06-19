<#
  selftest-final.ps1 — deterministic unit tests for the E5 additions:
    (a) PROMPT-CACHE telemetry: the pure Get-CacheUsage parser turns a claude
        `usage` block into hitRatio/warm (tolerating absent counters), and
        New-CacheEvent emits the EXACT PROTOCOL §2 `cache {hitRatio,warm}` shape.
    (b) Q&A SURFACE: Get-QuestionMarker detects a `QUESTION:` marker (and ignores
        ordinary prose); New-ReviewQuestionEvent / New-ReviewAnswerEvent emit the
        EXACT PROTOCOL §2 `review-question {turn,q,story?}` / `review-answer
        {turn,a}` shapes.
    (c) ANSWER INBOX: Read-AnswerInbox parses the PROTOCOL §1 answer.json shape
        ({ qid, kind, epic?, a }) and matches/consumes it for the open turn; a
        temp answer.json on disk is written, detected, and DELETED (consumed).

  ZERO quota: NOTHING here invokes claude; the decider is never reached. Every
  cache/usage/answer input is a CAPTURED string fed straight to the pure cores.
  Run: pwsh -NoProfile -File selftest-final.ps1
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

# Trip-wire: this MUST stay 0 for the whole run — proof no claude is invoked.
$script:claudeCalls = 0

# =====================================================================
# (a) PROMPT-CACHE TELEMETRY — Get-CacheUsage + New-CacheEvent
# =====================================================================
Write-Host "cache-usage parser + cache event (PROTOCOL §2):" -ForegroundColor Cyan

# 1a. A WARM result: cache_read=9000, input=1000 -> hitRatio 0.9, warm true.
#     Sample usage block exactly as `claude -p --output-format json` emits.
$usageWarm = '{ "input_tokens": 1000, "cache_read_input_tokens": 9000,
                "cache_creation_input_tokens": 0, "output_tokens": 200 }' | ConvertFrom-Json
$cwarm = Get-CacheUsage -Usage $usageWarm
Check "warm cacheRead"     $cwarm.CacheRead 9000
Check "warm input"         $cwarm.Input 1000
Check "warm hitRatio"      $cwarm.HitRatio 0.9
Check "warm warm flag"     $cwarm.Warm $true

# 1b. A COLD/first call: all input is creation, no read -> hitRatio 0, warm false.
$usageCold = '{ "input_tokens": 5000, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 5000, "output_tokens": 120 }' | ConvertFrom-Json
$ccold = Get-CacheUsage -Usage $usageCold
Check "cold cacheRead"     $ccold.CacheRead 0
Check "cold creation"      $ccold.CacheCreation 5000
Check "cold hitRatio"      $ccold.HitRatio 0.0
Check "cold warm flag"     $ccold.Warm $false

# 1c. ABSENT counters (older claude / text format): tolerate -> 0 / cold, no throw.
$usageBare = '{ "output_tokens": 50 }' | ConvertFrom-Json
$cbare = Get-CacheUsage -Usage $usageBare
Check "bare hitRatio zero" $cbare.HitRatio 0.0
Check "bare warm false"    $cbare.Warm $false
Check "bare cacheRead 0"   $cbare.CacheRead 0

# 1d. Whole RESULT object with a nested `usage` is unwrapped automatically.
$resultObj = '{ "type":"result", "is_error":false, "total_cost_usd":0.02,
                "usage": { "input_tokens": 2000, "cache_read_input_tokens": 6000 } }' | ConvertFrom-Json
$cwrap = Get-CacheUsage -Usage $resultObj
Check "wrapped cacheRead"  $cwrap.CacheRead 6000
Check "wrapped input"      $cwrap.Input 2000
Check "wrapped hitRatio"   $cwrap.HitRatio 0.75   # 6000/(6000+2000)
Check "wrapped warm"       $cwrap.Warm $true

# 1e. A raw JSON STRING is parsed directly (the parser is string-tolerant).
$cstr = Get-CacheUsage -Usage '{ "input_tokens": 100, "cache_read_input_tokens": 300 }'
Check "string hitRatio"    $cstr.HitRatio 0.75
Check "string warm"        $cstr.Warm $true

# 1f. camelCase variants accepted (defensive).
$ccamel = Get-CacheUsage -Usage '{ "inputTokens": 1000, "cacheReadInputTokens": 1000 }'
Check "camel hitRatio"     $ccamel.HitRatio 0.5
Check "camel warm"         $ccamel.Warm $true

# 1g. $null usage -> safe zero/cold (no divide, no throw).
$cnull = Get-CacheUsage -Usage $null
Check "null hitRatio"      $cnull.HitRatio 0.0
Check "null warm"          $cnull.Warm $false

# --- the cache EVENT must carry the EXACT PROTOCOL §2 shape -----------
$ce = New-CacheEvent -HitRatio $cwarm.HitRatio -Warm $cwarm.Warm
$jce = $ce | ConvertTo-Json -Compress
Check "cache event"        $ce.event 'cache'
Check "cache hitRatio"     $ce.hitRatio 0.9
Check "cache warm"         $ce.warm $true
Check "cache field count"  (@($ce.PSObject.Properties.Name)).Count 3
Check "cache json fields"  ($jce -match '"event":"cache"' -and $jce -match '"hitRatio":0.9' -and $jce -match '"warm":true') $true
Check "cache json no extra" ($jce -match 'cacheRead|input|cum|model') $false

# cold-call event shape too.
$ce0 = New-CacheEvent -HitRatio $ccold.HitRatio -Warm $ccold.Warm
$jce0 = $ce0 | ConvertTo-Json -Compress
Check "cache cold json"    ($jce0 -match '"warm":false' -and $jce0 -match '"hitRatio":0') $true

# =====================================================================
# (b) Q&A MARKER DETECTION + review-question / review-answer shapes
# =====================================================================
Write-Host "`nQUESTION marker + review-question/answer events (PROTOCOL §2):" -ForegroundColor Cyan

# 2a. a clean QUESTION marker (first line) -> extracted, trimmed.
$qText = "QUESTION: should the parser support hex literals like 0x1F?`nmore notes below"
$q1 = Get-QuestionMarker -Text $qText
Check "marker detected"    $q1 'should the parser support hex literals like 0x1F?'

# 2b. marker not on the first line, leading whitespace, case-insensitive prefix.
$qText2 = "## Failing / Next`n  question: pick base 10 or 16 default?"
$q2 = Get-QuestionMarker -Text $qText2
Check "marker mid-text"    $q2 'pick base 10 or 16 default?'

# 2c. ordinary prose mentioning "question" does NOT trip it (anchored prefix).
$q3 = Get-QuestionMarker -Text "I considered the question of precedence and fixed it."
Check "prose no false marker" ($null -eq $q3) $true

# 2d. empty / null text -> no marker.
Check "empty no marker"    ($null -eq (Get-QuestionMarker -Text '')) $true
Check "null no marker"     ($null -eq (Get-QuestionMarker -Text $null)) $true

# 2e. a bare 'QUESTION:' with no body -> no marker (nothing to ask).
Check "bare prefix no marker" ($null -eq (Get-QuestionMarker -Text 'QUESTION:   ')) $true

# 2f. only the FIRST marker is returned when several exist.
$qMulti = "QUESTION: first?`nQUESTION: second?"
Check "first marker wins"  (Get-QuestionMarker -Text $qMulti) 'first?'

# --- review-question event (PROTOCOL §2: {turn,q,story?}) -------------
$rq = New-ReviewQuestionEvent -Turn 4 -Q 'support hex literals?'
$jrq = $rq | ConvertTo-Json -Compress
Check "rq event"           $rq.event 'review-question'
Check "rq turn"            $rq.turn 4
Check "rq q"               $rq.q 'support hex literals?'
# story is OMITTED for the generic loop (no story) -> exactly 3 fields.
Check "rq no story field"  (@($rq.PSObject.Properties.Name) -contains 'story') $false
Check "rq field count"     (@($rq.PSObject.Properties.Name)).Count 3
Check "rq json fields"     ($jrq -match '"event":"review-question"' -and $jrq -match '"turn":4' -and $jrq -match '"q":"support hex literals\?"') $true
Check "rq json no story"   ($jrq -match '"story"') $false

# with a story (BMAD-style caller) -> story IS present.
$rqs = New-ReviewQuestionEvent -Turn 2 -Q 'q?' -Story '3-4-semantic-search'
$jrqs = $rqs | ConvertTo-Json -Compress
Check "rq story present"   $rqs.story '3-4-semantic-search'
Check "rq story json"      ($jrqs -match '"story":"3-4-semantic-search"') $true

# --- review-answer event (PROTOCOL §2: {turn,a}) ---------------------
$ra = New-ReviewAnswerEvent -Turn 4 -A 'Yes, support 0x hex literals.'
$jra = $ra | ConvertTo-Json -Compress
Check "ra event"           $ra.event 'review-answer'
Check "ra turn"            $ra.turn 4
Check "ra a"               $ra.a 'Yes, support 0x hex literals.'
Check "ra field count"     (@($ra.PSObject.Properties.Name)).Count 3
Check "ra json fields"     ($jra -match '"event":"review-answer"' -and $jra -match '"turn":4' -and $jra -match '"a":"Yes, support 0x hex literals\."') $true

# =====================================================================
# (c) ANSWER INBOX — Read-AnswerInbox parse + match + temp-file consume
# =====================================================================
Write-Host "`nanswer.json inbox parse + consume (PROTOCOL §1):" -ForegroundColor Cyan

# 3a. PROTOCOL §1 shape { qid, kind, epic?, a } matching the open turn (qid==turn).
$ans1 = '{ "qid": "4", "kind": "review", "a": "Yes, support hex." }'
$i1 = Read-AnswerInbox -Content $ans1 -Turn 4
Check "inbox matched"      $i1.Matched $true
Check "inbox answer"       $i1.A 'Yes, support hex.'
Check "inbox kind"         $i1.Kind 'review'
Check "inbox qid"          $i1.Qid '4'

# 3b. qid for a DIFFERENT turn -> not matched, answer withheld (left for its turn).
$i2 = Read-AnswerInbox -Content '{ "qid": "9", "a": "later" }' -Turn 4
Check "inbox wrong turn no match" $i2.Matched $false
Check "inbox wrong turn no answer" ($null -eq $i2.A) $true

# 3c. legacy `turn` key (instead of qid) also matches.
$i3 = Read-AnswerInbox -Content '{ "turn": 4, "a": "ok" }' -Turn 4
Check "inbox legacy turn match" $i3.Matched $true
Check "inbox legacy answer"     $i3.A 'ok'

# 3d. untargeted answer (no qid/turn) -> applies to whatever turn is open.
$i4 = Read-AnswerInbox -Content '{ "a": "use base 10" }' -Turn 7
Check "inbox untargeted match"  $i4.Matched $true
Check "inbox untargeted answer" $i4.A 'use base 10'

# 3e. no answer body -> nothing to consume.
$i5 = Read-AnswerInbox -Content '{ "qid": "4", "kind": "review" }' -Turn 4
Check "inbox no body no match"  $i5.Matched $false

# 3f. unparseable / empty content -> never invents an answer.
Check "inbox garbage no match"  (Read-AnswerInbox -Content 'not json' -Turn 4).Matched $false
Check "inbox empty no match"    (Read-AnswerInbox -Content '' -Turn 4).Matched $false
Check "inbox null no match"     (Read-AnswerInbox -Content $null -Turn 4).Matched $false

# --- full lifecycle on disk: WRITE temp answer.json -> detect -> review-answer
#     -> CONSUME (delete). Mirrors loop.ps1 Resolve-Question, but PURE (no claude).
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("loop-ans-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmpDir | Out-Null
try {
  $answerPath = Join-Path $tmpDir 'answer.json'
  $turn = 4
  $q = 'support hex literals?'
  # The UI's "answer from UI" writes the PROTOCOL §1 shape.
  '{ "qid": "4", "kind": "review", "a": "Yes — accept 0x hex literals." }' | Set-Content -Path $answerPath -Encoding utf8

  # detect a question (as the agent would have emitted it).
  $detectedQ = Get-QuestionMarker -Text "QUESTION: $q"
  Check "lifecycle question"  $detectedQ $q

  $rqEv = New-ReviewQuestionEvent -Turn $turn -Q $detectedQ
  Check "lifecycle rq event"  $rqEv.event 'review-question'

  # read + match the inbox.
  $content = Get-Content $answerPath -Raw
  $inbox = Read-AnswerInbox -Content $content -Turn $turn
  Check "lifecycle matched"   $inbox.Matched $true

  # emit review-answer and CONSUME the file (the loop deletes on match).
  $raEv = $null
  if ($inbox.Matched) {
    $raEv = New-ReviewAnswerEvent -Turn $turn -A $inbox.A
    Remove-Item $answerPath -Force
  }
  Check "lifecycle ra event"  $raEv.event 'review-answer'
  Check "lifecycle ra answer" $raEv.a 'Yes — accept 0x hex literals.'
  Check "lifecycle consumed (gone)" (Test-Path $answerPath) $false

  # after consumption, a re-read finds nothing (no double-answer).
  $after = Read-AnswerInbox -Content $(if (Test-Path $answerPath) { Get-Content $answerPath -Raw } else { $null }) -Turn $turn
  Check "lifecycle after-consume no match" $after.Matched $false
} finally { Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }

# =====================================================================
# (d) loop.ps1 WIRING — the seams exist and are isolated/stubbable
# =====================================================================
Write-Host "`nloop.ps1 wiring (E5 seams):" -ForegroundColor Cyan
$loopSrc = Get-Content -Path "$PSScriptRoot/loop.ps1" -Raw
Check "loop has -AutoDecide param"        ($loopSrc -match '\$AutoDecide') $true
Check "loop emits cache telemetry"        ($loopSrc -match 'Write-CacheTelemetry') $true
Check "loop parses cache usage"           ($loopSrc -match 'Get-CacheUsage') $true
Check "loop builds cache event"           ($loopSrc -match 'New-CacheEvent') $true
Check "loop resolves questions"           ($loopSrc -match 'Resolve-Question') $true
Check "loop emits review-question"        ($loopSrc -match 'New-ReviewQuestionEvent') $true
Check "loop emits review-answer"          ($loopSrc -match 'New-ReviewAnswerEvent') $true
Check "loop reads answer.json inbox"      ($loopSrc -match 'Read-AnswerInbox' -and $loopSrc -match 'answer\.json') $true
Check "loop has isolated decider seam"    ($loopSrc -match 'function Invoke-DeciderClaude') $true
Check "loop structures stable prefix"     ($loopSrc -match 'STABLE-PREFIX-FIRST|stable prefix|ContractBlock') $true

# =====================================================================
# ZERO-QUOTA proof
# =====================================================================
Write-Host "`nzero-quota proof:" -ForegroundColor Cyan
Check "ZERO claude calls" $script:claudeCalls 0

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL FINAL (E5) TESTS PASSED" -ForegroundColor Green; exit 0 }
else              { Write-Host "$fails FINAL TEST(S) FAILED" -ForegroundColor Red; exit 1 }
