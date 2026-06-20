<#
  gen_golden.ps1 — generate the deterministic golden corpus the Python port must match.

  Dot-sources the AUTHORITATIVE loopcore.ps1 and calls each New-*Event / New-Checkpoint
  with FIXED representative args and FIXED datetimes, then writes one JSON object per line
  (case-id + expected event object) to fixtures/golden_events.jsonl.

  Events whose builder lives inline in loop.ps1 (not loopcore) — iter/stop/parse_error/
  gate/model/cost-alert/verdict — are emitted here from their PROTOCOL.md §2 spec so the
  golden corpus is the single source of truth the Python builders are checked against.

  Run: pwsh -NoProfile -File legacy/gen_golden.ps1
#>

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# loopcore.ps1 is now a sibling in legacy/; the repo root is one level up.
$repoRoot = (Resolve-Path (Join-Path $here '..')).Path
. (Join-Path $here 'loopcore.ps1')

# The Python engine asserts against the COMMITTED corpus at engine/tests/fixtures/.
$fixturesDir = Join-Path $repoRoot 'engine/tests/fixtures'
New-Item -ItemType Directory -Force -Path $fixturesDir | Out-Null
$outFile = Join-Path $fixturesDir 'golden_events.jsonl'

# Fixed datetimes so timestamp output is deterministic.
$FIXED_NOW = [datetime]::Parse('2026-01-02T03:04:05Z', $null, [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal)
$FIXED_RESET = [datetime]::Parse('2026-01-02T09:00:00Z', $null, [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal)

$cases = [System.Collections.Generic.List[object]]::new()
function Add-Case([string]$id, $obj) { $cases.Add([ordered]@{ case = $id; expected = $obj }) }

# --- PROTOCOL §2 "Core" events (built inline in loop.ps1; spec'd here) ---
Add-Case 'iter' ([ordered]@{
  event = 'iter'; iter = 4; cost = 0.12; cum = 1.5; pass = 8; total = 10;
  best = 8; changed = $true; stale = 0; plateau = 1; regress = 0;
  action = 'continue'; reason = 'advance'
})
Add-Case 'stop' ([ordered]@{
  event = 'stop'; reason = 'all tests green at iter 4'; green = $true; iter = 4; cum = 1.5; bestPass = 8
})
Add-Case 'parse_error' ([ordered]@{ event = 'parse_error'; iter = 4 })
Add-Case 'gate' ([ordered]@{
  event = 'gate'; story = 'S1'; cum = 1.5; green = $true; pass = 8; fail = 0; total = 8;
  baselinePass = 6; stages = @([ordered]@{ name = 'test'; ok = $true; exit = 0 })
})
Add-Case 'model' ([ordered]@{ event = 'model'; phase = 'execute'; model = 'sonnet'; costPerTurn = 0.05 })
Add-Case 'cost_alert' ([ordered]@{ event = 'cost-alert'; pct = 80; cum = 2.4; ceiling = 3.0 })

# --- verdict (event shape mirrors ConvertFrom-VerdictJson OUTPUT) ---
Add-Case 'verdict' ([ordered]@{
  event = 'verdict'; item = 'roman'; pass = $false;
  failingCriteria = @('handles 0', 'rejects negatives'); evidence = 'two cases fail';
  nextAction = 'fix zero handling'; model = 'haiku'
})

# --- loopcore.ps1 New-*Event / New-Checkpoint (called directly) ---
Add-Case 'cache' (New-CacheEvent -HitRatio 0.123456 -Warm $true)

Add-Case 'plateau' (New-PlateauEvent -Item 'roman' -K 3)

Add-Case 'rollback' (New-RollbackEvent -Item 'roman' -ToIter 7 -BestPass 8 -Strike 2 -StrikeBudget 3)

Add-Case 'handoff' (New-HandoffEvent -Item 'roman' -Reason 'strikes exhausted' -Consecutive 3)

Add-Case 'phase_timeout' (New-PhaseTimeoutEvent -Label 'iter 4' -TimeoutSec 600)

Add-Case 'quota_hit_with_reset'    (New-QuotaHitEvent -Label 'dev' -Cum 12.5 -ResetAt $FIXED_RESET)
Add-Case 'quota_hit_without_reset' (New-QuotaHitEvent -Label 'dev' -Cum 12.5)

Add-Case 'quota_wait' (New-QuotaWaitEvent -Label 'dev' -Cum 12.5 -WaitSec 3600 -Probe 2 -ResetType 'five_hour' -Now $FIXED_NOW)
Add-Case 'quota_wait_no_type' (New-QuotaWaitEvent -Label 'dev' -Cum 12.5 -WaitSec 1800 -Probe 1 -Now $FIXED_NOW)

Add-Case 'quota_resume' (New-QuotaResumeEvent -Label 'dev' -Probe 3)

Add-Case 'cooperative_stop' (New-CooperativeStopEvent -Scope 'story' -Mode 'phase' -Stage 'dev-story' -Story 'S1' -Branch 'feat/x' -Cum 12.5)

Add-Case 'review_question_with_story'    (New-ReviewQuestionEvent -Turn 1 -Q 'Use UTC?' -Story 'S1')
Add-Case 'review_question_without_story' (New-ReviewQuestionEvent -Turn 2 -Q 'Drop seconds?')

Add-Case 'review_answer' (New-ReviewAnswerEvent -Turn 1 -A 'Yes, UTC.')

Add-Case 'checkpoint' (New-Checkpoint -Stage 'dev-story' -Story 'S1' -Branch 'feat/x' -MergeBase 'abc123' -CumUsd 12.34567 -Resume 'pwsh -File loop.ps1 -Resume' -UpdatedAt $FIXED_NOW)

# --- write one compact JSON object per line ---
$lines = foreach ($c in $cases) { $c | ConvertTo-Json -Compress -Depth 10 }
Set-Content -Path $outFile -Value $lines -Encoding utf8
Write-Host "wrote $($cases.Count) cases -> $outFile"
