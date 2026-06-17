<#
  loopcore.ps1 — pure, testable harness logic. Dot-sourced by loop.ps1 and by
  selftest.ps1. No side effects, no claude calls. This is where the
  "evaluation hardening" lives so it can be verified deterministically.
#>

function Get-TestHash {
  # Authoritative anti-cheat. Hash of all *.test.ts, order-stable.
  # Edit / skip / delete any test -> this string changes.
  $files = Get-ChildItem -Recurse -Filter *.test.ts | Sort-Object FullName
  if (-not $files) { return "" }
  ($files | Get-FileHash -Algorithm SHA256 | ForEach-Object Hash) -join ""
}

function Invoke-Gate {
  # The EVAL GATE. Runs the real tests; returns pass/fail + green-ness.
  $output = & bun test 2>&1 | Out-String
  $exit   = $LASTEXITCODE
  $pass   = if ($output -match '(\d+)\s+pass') { [int]$Matches[1] } else { 0 }
  $fail   = if ($output -match '(\d+)\s+fail') { [int]$Matches[1] } else { 0 }
  [pscustomobject]@{
    Green = ($exit -eq 0); Pass = $pass; Fail = $fail; Total = $pass + $fail; Raw = $output
  }
}

function Get-LoopDecision {
  # The verdict. Given fully-computed state, return what to do next.
  # Action is one of: 'stop' | 'rollback' | 'continue'. Pure — unit-tested.
  param(
    [bool]   $Green,
    [bool]   $Tampered,        # test-file hash changed
    [bool]   $CountDropped,    # total tests < baseline
    [bool]   $Blocked,         # agent wrote BLOCKED:
    [int]    $Pass,
    [int]    $BestPass,        # most passes seen so far
    [bool]   $Changed,         # working tree changed this iter
    [int]    $RegressCount,    # rollbacks so far
    [int]    $RegressLimit,
    [int]    $Plateau,         # consecutive changed-but-no-improvement iters
    [int]    $PlateauLimit,
    [int]    $Stale,           # consecutive no-change iters
    [int]    $StagnationLimit,
    [double] $Cum,
    [double] $Ceiling,
    [int]    $Iter,
    [int]    $MaxIters
  )

  # Priority order matters. Integrity checks beat success; success beats spend.
  if ($Tampered)     { return [pscustomobject]@{ Action='stop'; Green=$false; Reason='test files were modified (tamper)' } }
  if ($CountDropped) { return [pscustomobject]@{ Action='stop'; Green=$false; Reason='test count dropped (deleted/skipped tests)' } }
  if ($Green)        { return [pscustomobject]@{ Action='stop'; Green=$true;  Reason="all tests green at iter $Iter" } }
  if ($Blocked)      { return [pscustomobject]@{ Action='stop'; Green=$false; Reason='agent reported BLOCKED' } }
  if ($Cum -ge $Ceiling) { return [pscustomobject]@{ Action='stop'; Green=$false; Reason="cost ceiling `$$Ceiling reached" } }

  if ($Pass -lt $BestPass) {                       # regression = silent drift
    if (($RegressCount + 1) -ge $RegressLimit) {
      return [pscustomobject]@{ Action='stop'; Green=$false; Reason="repeated regressions ($($RegressCount+1)/$RegressLimit) — handoff" }
    }
    return [pscustomobject]@{ Action='rollback'; Green=$false; Reason="regression ($Pass < best $BestPass) — rolling back to best" }
  }

  if (-not $Changed -and $Stale -ge $StagnationLimit) {
    return [pscustomobject]@{ Action='stop'; Green=$false; Reason="stagnation: $Stale iters with no change" }
  }
  if ($Changed -and $Pass -eq $BestPass -and $Plateau -ge $PlateauLimit) {
    return [pscustomobject]@{ Action='stop'; Green=$false; Reason="plateau: $Plateau iters changed with no net progress" }
  }
  if ($Iter -ge $MaxIters) {
    return [pscustomobject]@{ Action='stop'; Green=$false; Reason="max iterations ($MaxIters) reached without green" }
  }
  return [pscustomobject]@{ Action='continue'; Green=$false; Reason='advance' }
}
