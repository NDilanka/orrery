<#
  selftest-gate.ps1 — deterministic unit tests for the E1 generalization:
  the multi-stage gate parser and the cost-alert threshold logic.

  NO claude calls, NO real test runners. Each "stage" command is a scriptblock
  that echoes a CAPTURED sample output and sets the exit code — so the parser /
  green-logic / cost-alert math are verified in isolation.

  Run: pwsh -NoProfile -File selftest-gate.ps1
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

# A stage whose Command is a scriptblock that prints $text and exits $code.
# Using a closure so each stage carries its own captured sample + exit code.
function Stage($name, $text, $code, $passPattern, $failPattern) {
  $t = $text; $c = $code
  @{
    Name        = $name
    Command     = { Write-Output $t; $global:LASTEXITCODE = $c }.GetNewClosure()
    PassPattern = $passPattern
    FailPattern = $failPattern
  }
}

# ---- captured sample outputs from real runners ----------------------------
$bunGreen = @"
bun test v1.3.11 (af24e281)

 3 pass
 0 fail
 12 expect() calls
Ran 3 tests across 1 files. [42.00ms]
"@

$bunRed = @"
bun test v1.3.11 (af24e281)

 1 pass
 2 fail
Ran 3 tests across 1 files. [55.00ms]
"@

# vitest / jest style: "Tests:  2 failed, 5 passed, 7 total"
$vitest = @"
 FAIL  src/calc.test.ts > handles precedence
Test Files  1 failed (1)
     Tests  2 failed | 5 passed (7)
  Start at  10:00:00
  Duration  812ms
"@

$jest = @"
Tests:       1 failed, 8 passed, 9 total
Snapshots:   0 total
Time:        2.13 s
Ran all test suites.
"@

# pytest style: "===== 5 passed in 0.12s ====="  (and a failing variant)
$pytestGreen = @"
============================= test session starts =============================
collected 5 items

test_calc.py .....                                                       [100%]

============================== 5 passed in 0.12s ==============================
"@

$pytestRed = @"
============================= test session starts =============================
collected 5 items

test_calc.py ..F..                                                       [100%]

========================= 1 failed, 4 passed in 0.20s =========================
"@

# go test style: per-package "ok" / "FAIL" lines + counts via -v summary.
# Common pattern people parse: count "--- PASS:" / "--- FAIL:" lines.
$goTest = @"
=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestSub
--- PASS: TestSub (0.00s)
=== RUN   TestMul
--- FAIL: TestMul (0.00s)
FAIL
exit status 1
FAIL    example/calc    0.012s
"@

Write-Host "Gate-parser & cost-alert self-test:`n"

# === single-stage parsing, multiple runner dialects ========================

$bun = Get-GateCounts -Text $bunGreen -PassPattern '(\d+)\s+pass' -FailPattern '(\d+)\s+fail'
Check "bun green pass"   $bun.Pass 3
Check "bun green fail"   $bun.Fail 0

$bunR = Get-GateCounts -Text $bunRed -PassPattern '(\d+)\s+pass' -FailPattern '(\d+)\s+fail'
Check "bun red pass"     $bunR.Pass 1
Check "bun red fail"     $bunR.Fail 2

# vitest prints "Test Files 1 failed" before the per-test "Tests 2 failed | 5 passed";
# a real config anchors the counts to the "Tests" line so the file-level count
# doesn't win. Demonstrates patterns are caller-tunable per runner.
$vit = Get-GateCounts -Text $vitest -PassPattern '(\d+)\s+passed' -FailPattern 'Tests\s+(\d+)\s+failed'
Check "vitest pass"      $vit.Pass 5
Check "vitest fail"      $vit.Fail 2

$jst = Get-GateCounts -Text $jest -PassPattern '(\d+)\s+passed' -FailPattern '(\d+)\s+failed'
Check "jest pass"        $jst.Pass 8
Check "jest fail"        $jst.Fail 1

$pyG = Get-GateCounts -Text $pytestGreen -PassPattern '(\d+)\s+passed' -FailPattern '(\d+)\s+failed'
Check "pytest green pass" $pyG.Pass 5
Check "pytest green fail" $pyG.Fail 0
Check "pytest green nomatch-fail-still-matched" $pyG.Matched $true

$pyR = Get-GateCounts -Text $pytestRed -PassPattern '(\d+)\s+passed' -FailPattern '(\d+)\s+failed'
Check "pytest red pass"  $pyR.Pass 4
Check "pytest red fail"  $pyR.Fail 1

# go test reports per-test "--- PASS:" / "--- FAIL:" lines, not a single count.
# A go config counts those lines; the gate's regex extraction still works when a
# numeric summary is present, so here we verify line-counting parity directly.
$goPass = ([regex]::Matches($goTest, '(?m)^--- PASS:')).Count
$goFail = ([regex]::Matches($goTest, '(?m)^--- FAIL:')).Count
Check "go-test pass count" $goPass 2
Check "go-test fail count" $goFail 1

# === Invoke-Gate end to end via scriptblock stages =========================

# single green stage -> Green=$true, totals from the stage
$g1 = Invoke-Gate -Stages @( (Stage 'test' $bunGreen 0 '(\d+)\s+pass' '(\d+)\s+fail') )
Check "single-stage green"   $g1.Green $true
Check "single-stage pass"    $g1.Pass  3
Check "single-stage fail"    $g1.Fail  0
Check "single-stage total"   $g1.Total 3
Check "single-stage count"   $g1.Stages.Count 1

# single red stage (exit 1) -> Green=$false
$g2 = Invoke-Gate -Stages @( (Stage 'test' $bunRed 1 '(\d+)\s+pass' '(\d+)\s+fail') )
Check "single-stage red green" $g2.Green $false
Check "single-stage red pass"  $g2.Pass  1
Check "single-stage red fail"  $g2.Fail  2

# multi-stage all-green: codegen(no counts,0) + lint(no counts,0) + test(green,0)
$g3 = Invoke-Gate -Stages @(
  (Stage 'codegen' "codegen ok`nGenerated 4 files." 0 '(\d+)\s+pass' '(\d+)\s+fail'),
  (Stage 'lint'    "lint: 0 problems"               0 '(\d+)\s+pass' '(\d+)\s+fail'),
  (Stage 'test'    $bunGreen                         0 '(\d+)\s+pass' '(\d+)\s+fail')
)
Check "multi all-green green"  $g3.Green $true
Check "multi all-green pass"   $g3.Pass  3      # counts come from LAST stage with counts
Check "multi all-green total"  $g3.Total 3
Check "multi all-green stages" $g3.Stages.Count 3

# multi-stage: a PRE stage fails (lint exit 1) but test stage is green ->
# Green MUST be false (all stages must exit 0).
$g4 = Invoke-Gate -Stages @(
  (Stage 'codegen' "codegen ok"          0 '(\d+)\s+pass' '(\d+)\s+fail'),
  (Stage 'lint'    "lint: 3 problems"    1 '(\d+)\s+pass' '(\d+)\s+fail'),
  (Stage 'test'    $bunGreen             0 '(\d+)\s+pass' '(\d+)\s+fail')
)
Check "multi lint-fail green"   $g4.Green $false
Check "multi lint-fail pass"    $g4.Pass  3       # test still reported 3 pass
Check "multi lint-fail lint-ok" ($g4.Stages | Where-Object name -eq 'lint').ok $false
Check "multi lint-fail test-ok" ($g4.Stages | Where-Object name -eq 'test').ok $true

# pre-stage with no counts that FAILS, test never reached counts -> Pass=0
$g5 = Invoke-Gate -Stages @(
  (Stage 'codegen' "ERROR: type mismatch" 1 '(\d+)\s+pass' '(\d+)\s+fail')
)
Check "codegen-only fail green" $g5.Green $false
Check "codegen-only fail pass"  $g5.Pass  0
Check "codegen-only fail total" $g5.Total 0

# default stages (no -Stages) falls back to the single bun-test stage shape.
# We don't run real bun here; just assert the function tolerates an empty array
# by checking the fallback Stage name is 'test' via reflection of behavior:
# (call with an explicit empty array -> default kicks in -> it would try to run
#  `bun test`. To avoid the real runner we instead assert the parser default by
#  passing a scriptblock named 'test'.)
$g6 = Invoke-Gate -Stages @( (Stage 'test' $bunGreen 0 $null $null) )
Check "default-pattern pass"  $g6.Pass 3
Check "default-pattern fail"  $g6.Fail 0

# === cost-alert threshold logic ============================================

# Below 50% -> nothing fires.
$c0 = Update-CostAlert -Cum 1.0 -Ceiling 3.0 -Thresholds @(50,80,100) -Fired @()
Check "cost <50 none" $c0.Newly.Count 0

# Cross 50% once.
$c1 = Update-CostAlert -Cum 1.5 -Ceiling 3.0 -Thresholds @(50,80,100) -Fired @()
Check "cost 50 fires"        $c1.Newly.Count 1
Check "cost 50 value"        $c1.Newly[0]    50
Check "cost 50 in fired"     ($c1.Fired -contains 50) $true

# Re-check at 50% with 50 already fired -> does NOT fire again.
$c1b = Update-CostAlert -Cum 1.5 -Ceiling 3.0 -Thresholds @(50,80,100) -Fired $c1.Fired
Check "cost 50 once only" $c1b.Newly.Count 0

# Jump straight from <50 to 100 -> all three fire once, in ascending order.
$c2 = Update-CostAlert -Cum 3.0 -Ceiling 3.0 -Thresholds @(50,80,100) -Fired @()
Check "cost jump fires 3"   $c2.Newly.Count 3
Check "cost jump order 50"  $c2.Newly[0]    50
Check "cost jump order 80"  $c2.Newly[1]    80
Check "cost jump order 100" $c2.Newly[2]    100

# Over ceiling stays at 100 (no double-fire, no >100 threshold).
$c3 = Update-CostAlert -Cum 5.0 -Ceiling 3.0 -Thresholds @(50,80,100) -Fired $c2.Fired
Check "cost over-ceiling none" $c3.Newly.Count 0

# Sequential walk: 50 then 80 then 100, each fires exactly once across calls.
$fired = @()
$s1 = Update-CostAlert -Cum 1.5 -Ceiling 3.0 -Fired $fired; $fired = $s1.Fired
$s2 = Update-CostAlert -Cum 2.4 -Ceiling 3.0 -Fired $fired; $fired = $s2.Fired
$s3 = Update-CostAlert -Cum 3.0 -Ceiling 3.0 -Fired $fired; $fired = $s3.Fired
Check "walk 50 step"  $s1.Newly[0] 50
Check "walk 80 step"  $s2.Newly[0] 80
Check "walk 100 step" $s3.Newly[0] 100
Check "walk total fired" $fired.Count 3

# Zero/negative ceiling -> never fires (avoid div-by-zero).
$cz = Update-CostAlert -Cum 5.0 -Ceiling 0.0 -Fired @()
Check "cost zero-ceiling none" $cz.Newly.Count 0

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL GATE/COST TESTS PASSED" -ForegroundColor Green; exit 0 }
else              { Write-Host "$fails GATE/COST TEST(S) FAILED" -ForegroundColor Red; exit 1 }
