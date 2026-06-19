<#
  selftest-verify.ps1 — deterministic unit tests for the E2 additions:
    - the verdict PARSER (ConvertFrom-VerdictJson) -> exact Protocol `verdict` JSON
    - the frozen-contract extractor (ConvertTo-ContractCriteria)
    - the per-file hash-map tamper detector (Compare-HashMap) -> NAMES the file
    - the model-tier selection logic (Get-ModelForPhase)

  ZERO quota: NONE of these invoke claude or a real test runner. The judge output
  is a CAPTURED string fed straight to the parser; the hash maps are built in
  code. Run: pwsh -NoProfile -File selftest-verify.ps1
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

Write-Host "Verifier / contract / hash-map / model-tier self-test:`n"

# =====================================================================
# 1. VERDICT PARSER -> exact Protocol §2 `verdict` event JSON
# =====================================================================
Write-Host "verdict parser:" -ForegroundColor Cyan

# --- 1a. a clean PASS verdict (judge emits bare JSON) ----------------
$judgePass = '{ "pass": true, "failingCriteria": [], "evidence": "all AC met", "nextAction": "" }'
$v1 = ConvertFrom-VerdictJson -RawText $judgePass -Item 'TASK.md' -Model 'haiku'
Check "pass event name"      $v1.event 'verdict'
Check "pass item"            $v1.item  'TASK.md'
Check "pass bool"            $v1.pass  $true
Check "pass model"           $v1.model 'haiku'
Check "pass failing count"   $v1.failingCriteria.Count 0
Check "pass evidence"        $v1.evidence 'all AC met'

# the emitted JSON must carry the EXACT Protocol field names
$j1 = $v1 | ConvertTo-Json -Compress
Check "json has event"           ($j1 -match '"event":"verdict"') $true
Check "json has item"            ($j1 -match '"item":"TASK\.md"') $true
Check "json has pass"            ($j1 -match '"pass":true') $true
Check "json has failingCriteria" ($j1 -match '"failingCriteria"') $true
Check "json has model"           ($j1 -match '"model":"haiku"') $true
Check "json no snake failing"    ($j1 -match 'failing_criteria') $false
Check "json no snake next"       ($j1 -match 'next_action') $false

# --- 1b. a REFUTE verdict, judge wrapped its JSON in a ```json fence + prose ---
$judgeFail = @"
Here is my assessment after reviewing the diff against the contract.

``````json
{ "pass": false,
  "failingCriteria": ["2^3^2 == 512 not handled", "unary minus missing"],
  "evidence": "diff only adds + and -, no exponent operator",
  "nextAction": "implement right-associative ^ in src/calc.ts" }
``````
"@
$v2 = ConvertFrom-VerdictJson -RawText $judgeFail -Item 'TASK.calc.md' -Model 'haiku'
Check "refute pass"            $v2.pass  $false
Check "refute failing count"   $v2.failingCriteria.Count 2
Check "refute failing[0]"      $v2.failingCriteria[0] '2^3^2 == 512 not handled'
Check "refute failing[1]"      $v2.failingCriteria[1] 'unary minus missing'
Check "refute nextAction"      $v2.nextAction 'implement right-associative ^ in src/calc.ts'
$j2 = $v2 | ConvertTo-Json -Compress
Check "refute json pass false" ($j2 -match '"pass":false') $true

# --- 1c. snake_case judge output is accepted (field-name tolerance) ---
$judgeSnake = '{ "verdict": "fail", "failing_criteria": ["x"], "evidence": "e", "next_action": "n" }'
$v3 = ConvertFrom-VerdictJson -RawText $judgeSnake -Item 'k' -Model 'sonnet'
Check "snake verdict->fail"    $v3.pass  $false
Check "snake failing[0]"       $v3.failingCriteria[0] 'x'
Check "snake nextAction"       $v3.nextAction 'n'

# --- 1d. fail-closed: a pass=true that still lists failing criteria is a FAIL ---
$contradiction = '{ "pass": true, "failingCriteria": ["still broken"] }'
$v4 = ConvertFrom-VerdictJson -RawText $contradiction -Item 'k' -Model 'haiku'
Check "contradiction -> fail"  $v4.pass $false

# --- 1e. fail-closed: unparseable judge body cannot mint a green ---
$v5 = ConvertFrom-VerdictJson -RawText 'I could not produce JSON, sorry.' -Item 'k' -Model 'haiku'
Check "garbage -> fail"        $v5.pass $false
Check "garbage failing names"  ($v5.failingCriteria.Count -ge 1) $true

# --- 1f. string "true" / "yes" accepted as pass ---
$v6 = ConvertFrom-VerdictJson -RawText '{ "pass": "true", "failingCriteria": [] }' -Item 'k' -Model 'haiku'
Check "string-true -> pass"    $v6.pass $true

# =====================================================================
# 2. FROZEN-CONTRACT EXTRACTOR (parse AC from a TaskFile)
# =====================================================================
Write-Host "`ncontract extractor:" -ForegroundColor Cyan

# 2a. "## Acceptance Criteria" with mixed bullets / checkboxes / numbers,
#     closed by the next heading (which must NOT be captured).
$taskAC = @"
# TASK

## Goal
make it work

## Acceptance Criteria
- 401 on expired token
- [ ] tests are green
- [x] handles unary minus
1. supports parentheses

## Working agreement
do not edit tests
"@
$c1 = ConvertTo-ContractCriteria -Text $taskAC
Check "AC count"        $c1.Count 4
Check "AC[0]"           $c1[0] '401 on expired token'
Check "AC[1] checkbox"  $c1[1] 'tests are green'
Check "AC[2] checked"   $c1[2] 'handles unary minus'
Check "AC[3] numbered"  $c1[3] 'supports parentheses'

# 2b. "## Definition of done" (the real TASK.md shape) with a one-line prose AC.
# Single-quoted here-string so the literal backtick-fenced `bun test` survives.
$taskDoD = @'
## Definition of done
`bun test` reports 0 failures with all three original tests present.

## Working agreement
1. read the file
'@
$c2 = @(ConvertTo-ContractCriteria -Text $taskDoD)
Check "DoD count"   $c2.Count 1
Check "DoD[0] prose" ($c2[0] -match 'reports 0 failures') $true

# 2c. heading is case-insensitive.
$c3 = ConvertTo-ContractCriteria -Text "## DEFINITION OF DONE`n- done when green"
Check "DoD case-insensitive" $c3.Count 1

# 2d. no such section -> empty.
$c4 = ConvertTo-ContractCriteria -Text "# TASK`n## Goal`njust do it"
Check "no AC section -> 0" $c4.Count 0

# 2e. real TASK.md on disk parses its Definition-of-done.
$realTask = Get-Content -Path "$PSScriptRoot/TASK.md" -Raw
$c5 = ConvertTo-ContractCriteria -Text $realTask
Check "real TASK.md AC found" ($c5.Count -ge 1) $true

# =====================================================================
# 3. PER-FILE HASH-MAP TAMPER DETECTION (name WHICH file changed)
# =====================================================================
Write-Host "`nhash-map tamper detector:" -ForegroundColor Cyan

# Build baseline + variants as plain in-memory maps (no disk, no runner).
$base = [ordered]@{ 'src/a.test.ts' = 'AAA'; 'src/b.test.ts' = 'BBB'; 'src/c.test.ts' = 'CCC' }

# 3a. identical -> not tampered.
$d0 = Compare-HashMap -Baseline $base -Current ([ordered]@{ 'src/a.test.ts'='AAA'; 'src/b.test.ts'='BBB'; 'src/c.test.ts'='CCC' })
Check "identical not tampered" $d0.Tampered $false
Check "identical reason empty" ($d0.Reason -eq '') $true

# 3b. one file MODIFIED -> tampered, reason NAMES it.
$d1 = Compare-HashMap -Baseline $base -Current ([ordered]@{ 'src/a.test.ts'='AAA'; 'src/b.test.ts'='ZZZ'; 'src/c.test.ts'='CCC' })
Check "modified tampered"   $d1.Tampered $true
Check "modified names file" $d1.Changed[0] 'src/b.test.ts'
Check "modified reason has file" ($d1.Reason -match 'modified src/b\.test\.ts') $true

# 3c. one file DELETED (skipped) -> tampered, named as deleted.
$d2 = Compare-HashMap -Baseline $base -Current ([ordered]@{ 'src/a.test.ts'='AAA'; 'src/c.test.ts'='CCC' })
Check "deleted tampered"    $d2.Tampered $true
Check "deleted names file"  $d2.Removed[0] 'src/b.test.ts'
Check "deleted reason has file" ($d2.Reason -match 'deleted src/b\.test\.ts') $true

# 3d. a NEW locked file added -> tampered, named as added.
$d3 = Compare-HashMap -Baseline $base -Current ([ordered]@{ 'src/a.test.ts'='AAA'; 'src/b.test.ts'='BBB'; 'src/c.test.ts'='CCC'; 'src/d.test.ts'='DDD' })
Check "added tampered"   $d3.Tampered $true
Check "added names file" $d3.Added[0] 'src/d.test.ts'

# 3e. the digest collapses a map to the old single-string hash (back-compat).
$dig1 = Get-HashMapDigest -Map $base
$dig2 = Get-HashMapDigest -Map ([ordered]@{ 'src/c.test.ts'='CCC'; 'src/a.test.ts'='AAA'; 'src/b.test.ts'='BBB' })
Check "digest order-stable" ($dig1 -eq $dig2) $true
Check "digest changes on tamper" ($dig1 -ne (Get-HashMapDigest -Map ([ordered]@{ 'src/a.test.ts'='AAA'; 'src/b.test.ts'='ZZZ'; 'src/c.test.ts'='CCC' }))) $true

# 3f. real map over this repo's *.test.ts files is per-file and non-empty.
$realMap = Get-TestHashMap -LockGlob '*.test.ts' -BasePath $PSScriptRoot
Check "real map non-empty" ($realMap.Count -ge 1) $true
$selfDiff = Compare-HashMap -Baseline $realMap -Current $realMap
Check "real map vs self clean" $selfDiff.Tampered $false

# =====================================================================
# 4. MODEL-TIER SELECTION LOGIC
# =====================================================================
Write-Host "`nmodel-tier selection:" -ForegroundColor Cyan

# 4a. defaults when no map supplied.
Check "default discover" (Get-ModelForPhase -Phase 'discover' -Models $null) 'haiku'
Check "default execute"  (Get-ModelForPhase -Phase 'execute'  -Models $null) 'sonnet'
Check "default judge"    (Get-ModelForPhase -Phase 'judge'    -Models $null) 'haiku'
Check "default hard"     (Get-ModelForPhase -Phase 'hard'     -Models $null) 'opus'

# 4b. user override wins.
$m = @{ execute = 'opus'; judge = 'sonnet' }
Check "override execute" (Get-ModelForPhase -Phase 'execute' -Models $m) 'opus'
Check "override judge"   (Get-ModelForPhase -Phase 'judge'   -Models $m) 'sonnet'
# unspecified phase falls back to default
Check "partial keeps default discover" (Get-ModelForPhase -Phase 'discover' -Models $m) 'haiku'

# 4c. case-insensitive phase name.
Check "phase case-insensitive" (Get-ModelForPhase -Phase 'EXECUTE' -Models $null) 'sonnet'

# 4d. unknown phase -> safe middle tier.
Check "unknown phase -> sonnet" (Get-ModelForPhase -Phase 'frobnicate' -Models $null) 'sonnet'

# =====================================================================
# 5. DECISION CORE: verifier-refuted green does NOT stop-green
# =====================================================================
Write-Host "`ndecision: refuted green:" -ForegroundColor Cyan

function Dv($over) {
  $p = @{
    Green=$true; Tampered=$false; CountDropped=$false; Blocked=$false;
    Pass=5; BestPass=5; Changed=$true;
    RegressCount=0; RegressLimit=3; Plateau=0; PlateauLimit=3;
    Stale=0; StagnationLimit=2; Cum=0.0; Ceiling=3.0; Iter=2; MaxIters=15;
    VerifierRefuted=$false
  }
  foreach ($k in $over.Keys) { $p[$k] = $over[$k] }
  Get-LoopDecision @p
}
# green + not refuted -> stop green (unchanged default behavior).
Check "green not refuted stops"  (Dv @{}).Action 'stop'
Check "green not refuted is green" (Dv @{}).Green $true
# green + refuted -> NOT a green stop; continues to feed criteria back.
Check "green refuted not stop-green" (Dv @{ VerifierRefuted=$true }).Green $false
Check "green refuted continues"      (Dv @{ VerifierRefuted=$true }).Action 'continue'
# refuted green at max-iters still hands off (per stop rules).
Check "refuted green max-iters stop" (Dv @{ VerifierRefuted=$true; Iter=15; MaxIters=15 }).Action 'stop'
# tamper still beats a refuted green.
Check "tamper beats refuted green"   (Dv @{ VerifierRefuted=$true; Tampered=$true }).Action 'stop'

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL VERIFY/CONTRACT/HASH/MODEL TESTS PASSED" -ForegroundColor Green; exit 0 }
else              { Write-Host "$fails VERIFY TEST(S) FAILED" -ForegroundColor Red; exit 1 }
