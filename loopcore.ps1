<#
  loopcore.ps1 — pure, testable harness logic. Dot-sourced by loop.ps1 and by
  selftest.ps1 / selftest-gate.ps1. No side effects, no claude calls. This is
  where the "evaluation hardening" lives so it can be verified deterministically.

  E1 generalization: the gate is now multi-stage and the lock glob is a param,
  so the SAME engine can drive arbitrary user loops (vitest/jest/pytest/go test
  /…), not just the Bun demo. Defaults reproduce the original single `bun test`
  behavior exactly.
#>

function Get-TestHash {
  # Authoritative anti-cheat. Hash of all locked files, order-stable.
  # Edit / skip / delete any locked file -> this string changes.
  # -LockGlob lets any project name its own locked test files (default *.test.ts).
  param([string] $LockGlob = '*.test.ts')
  $files = Get-ChildItem -Recurse -Filter $LockGlob -File -ErrorAction SilentlyContinue | Sort-Object FullName
  if (-not $files) { return "" }
  ($files | Get-FileHash -Algorithm SHA256 | ForEach-Object Hash) -join ""
}

function Get-GateCounts {
  # PURE parser: extract pass/fail counts from a captured test-runner string.
  # Unit-tested against real bun/vitest/jest/pytest/go-test outputs. No I/O.
  # Returns @{ Pass; Fail; Matched } where Matched=$true if either pattern hit.
  param(
    [string] $Text,
    [string] $PassPattern,
    [string] $FailPattern
  )
  $pass = 0; $fail = 0; $matched = $false
  if ($PassPattern -and $Text -match $PassPattern) { $pass = [int]$Matches[1]; $matched = $true }
  if ($FailPattern -and $Text -match $FailPattern) { $fail = [int]$Matches[1]; $matched = $true }
  [pscustomobject]@{ Pass = $pass; Fail = $fail; Matched = $matched }
}

function Invoke-Gate {
  # The EVAL GATE — now MULTI-STAGE. Runs an ordered array of stages; each stage
  # is @{ Name; Command; PassPattern; FailPattern }. A stage's Command may be a
  # string (run via the shell) or a scriptblock (run directly — used by tests to
  # inject captured output without spawning a real runner).
  #
  # Green  = every stage exited 0.
  # Pass/Fail/Total come from the LAST stage that reported any counts (so a
  # codegen/lint pre-stage that prints no counts does not zero the totals; the
  # real test stage wins). Stages collects per-stage {name; ok; exit}.
  #
  # Default stages reproduce today's single `bun test` exactly, so existing
  # callers and the roman/calc demo are unchanged.
  param(
    [object[]] $Stages
  )

  if (-not $Stages -or $Stages.Count -eq 0) {
    $Stages = @(
      @{ Name = 'test'; Command = 'bun test'; PassPattern = '(\d+)\s+pass'; FailPattern = '(\d+)\s+fail' }
    )
  }

  $stageResults = @()
  $allGreen     = $true
  $lastCounts   = $null
  $rawParts     = @()

  foreach ($s in $Stages) {
    $name        = $s.Name
    $cmd         = $s.Command
    $passPattern = if ($null -ne $s.PassPattern) { $s.PassPattern } else { '(\d+)\s+pass' }
    $failPattern = if ($null -ne $s.FailPattern) { $s.FailPattern } else { '(\d+)\s+fail' }

    if ($cmd -is [scriptblock]) {
      # Test/extension hook: scriptblock returns its captured output; it may set
      # $script:LASTEXITCODE itself. This path runs NO external process by default.
      $output = & $cmd 2>&1 | Out-String
      $exit   = $LASTEXITCODE
    } else {
      $global:LASTEXITCODE = 0
      $output = & cmd /c "$cmd 2>&1" | Out-String
      $exit   = $LASTEXITCODE
    }
    if ($null -eq $exit) { $exit = 0 }

    $counts = Get-GateCounts -Text $output -PassPattern $passPattern -FailPattern $failPattern
    if ($counts.Matched) { $lastCounts = $counts }

    $ok = ($exit -eq 0)
    if (-not $ok) { $allGreen = $false }

    $stageResults += [pscustomobject]@{ name = $name; ok = $ok; exit = $exit }
    $rawParts     += "### stage '$name' (exit=$exit)`n$output"
  }

  $pass = if ($lastCounts) { $lastCounts.Pass } else { 0 }
  $fail = if ($lastCounts) { $lastCounts.Fail } else { 0 }

  [pscustomobject]@{
    Green  = $allGreen
    Pass   = $pass
    Fail   = $fail
    Total  = $pass + $fail
    Stages = $stageResults
    Raw    = ($rawParts -join "`n")
  }
}

function Update-CostAlert {
  # PURE cost-threshold tracker. Given cumulative spend, ceiling, and the set of
  # thresholds already fired, return any NEW thresholds crossed (ascending) plus
  # the updated fired-set. Fires each of 50/80/100 exactly once, in order.
  # No I/O — the caller prints/logs the returned alerts. Unit-tested.
  param(
    [double] $Cum,
    [double] $Ceiling,
    [int[]]  $Thresholds = @(50, 80, 100),
    [int[]]  $Fired = @()
  )
  $newly = @()
  $firedSet = [System.Collections.Generic.HashSet[int]]::new()
  foreach ($f in $Fired) { [void]$firedSet.Add($f) }

  if ($Ceiling -gt 0) {
    $pct = ($Cum / $Ceiling) * 100.0
    foreach ($t in ($Thresholds | Sort-Object)) {
      if ($pct -ge $t -and -not $firedSet.Contains($t)) {
        $newly += $t
        [void]$firedSet.Add($t)
      }
    }
  }

  [pscustomobject]@{
    Newly = @($newly)
    Fired = @($firedSet | Sort-Object)
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
