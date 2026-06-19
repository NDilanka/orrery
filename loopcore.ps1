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
  # NOTE: kept for back-compat; the engine now uses the per-file MAP below and
  # derives this single string from it via Get-HashMapDigest.
  param([string] $LockGlob = '*.test.ts')
  $files = Get-ChildItem -Recurse -Filter $LockGlob -File -ErrorAction SilentlyContinue | Sort-Object FullName
  if (-not $files) { return "" }
  ($files | Get-FileHash -Algorithm SHA256 | ForEach-Object Hash) -join ""
}

function Get-TestHashMap {
  # E2 hash-lock hardening. Build a PER-FILE map (relativePath -> sha256) over
  # the lock glob, so tamper detection can name WHICH file changed. Pure-ish:
  # the only I/O is reading the locked files; no claude, no test runner.
  # Returns an ordered hashtable path->hash. -BasePath makes the keys relative
  # & stable so the map is comparable across machines / cwd.
  param(
    [string] $LockGlob = '*.test.ts',
    [string] $BasePath = (Get-Location).Path
  )
  $map = [ordered]@{}
  $files = Get-ChildItem -Path $BasePath -Recurse -Filter $LockGlob -File -ErrorAction SilentlyContinue | Sort-Object FullName
  foreach ($f in $files) {
    $rel = $f.FullName
    try { $rel = [System.IO.Path]::GetRelativePath($BasePath, $f.FullName) } catch {}
    $rel = $rel -replace '\\', '/'
    $map[$rel] = (Get-FileHash -Path $f.FullName -Algorithm SHA256).Hash
  }
  $map
}

function Get-HashMapDigest {
  # Collapse a per-file hash map into the single order-stable string that the
  # old Get-TestHash produced, so existing callers / baselines keep working.
  param([System.Collections.IDictionary] $Map)
  if (-not $Map -or $Map.Count -eq 0) { return "" }
  ($Map.Keys | Sort-Object | ForEach-Object { $Map[$_] }) -join ""
}

function Compare-HashMap {
  # PURE tamper detector. Diff a baseline per-file hash map against the current
  # one and report exactly WHICH files were modified / added / removed. The
  # engine keeps its existing tamper->stop behavior; this just names the file in
  # the reason string. Unit-tested, no I/O.
  #
  # Returns @{ Tampered=<bool>; Changed=[..]; Added=[..]; Removed=[..]; Reason=<str> }
  # where Reason is "" when nothing changed.
  param(
    [System.Collections.IDictionary] $Baseline,
    [System.Collections.IDictionary] $Current
  )
  $base = if ($Baseline) { $Baseline } else { [ordered]@{} }
  $cur  = if ($Current)  { $Current }  else { [ordered]@{} }

  $changed = @(); $added = @(); $removed = @()
  foreach ($k in $base.Keys) {
    if (-not $cur.Contains($k)) { $removed += $k }
    elseif ($cur[$k] -ne $base[$k]) { $changed += $k }
  }
  foreach ($k in $cur.Keys) {
    if (-not $base.Contains($k)) { $added += $k }
  }
  $changed = @($changed | Sort-Object)
  $added   = @($added   | Sort-Object)
  $removed = @($removed | Sort-Object)

  $tampered = ($changed.Count + $added.Count + $removed.Count) -gt 0
  $reason = ""
  if ($tampered) {
    $parts = @()
    if ($changed.Count) { $parts += "modified " + ($changed -join ', ') }
    if ($removed.Count) { $parts += "deleted "  + ($removed -join ', ') }
    if ($added.Count)   { $parts += "added "     + ($added   -join ', ') }
    $reason = "locked test file(s) " + ($parts -join '; ')
  }

  [pscustomobject]@{
    Tampered = $tampered
    Changed  = $changed
    Added    = $added
    Removed  = $removed
    Reason   = $reason
  }
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

function Get-ModelForPhase {
  # E2 model tiering. Pick the model for a phase from a tier map, falling back
  # to sensible defaults. Phases: discover|execute|judge|hard. Pure — no I/O.
  # Returns the tier string ('haiku'|'sonnet'|'opus' by default, but any string
  # the user supplies is honored so custom aliases work).
  param(
    [string] $Phase,
    [System.Collections.IDictionary] $Models
  )
  $defaults = @{ discover = 'haiku'; execute = 'sonnet'; judge = 'haiku'; hard = 'opus' }
  $key = "$Phase".ToLower()
  if ($Models -and $Models.Contains($key) -and $Models[$key]) { return [string]$Models[$key] }
  if ($defaults.ContainsKey($key)) { return $defaults[$key] }
  return 'sonnet'   # unknown phase -> safe middle tier
}

function ConvertTo-ContractCriteria {
  # E2 frozen-contract extractor. Parse acceptance criteria from a TaskFile's
  # text. Looks for an "## Acceptance Criteria" or "## Definition of done"
  # section (case-insensitive) and collects its list / checkbox / line items
  # until the next "## " heading. Pure — operates on a string, no file I/O.
  #
  # Recognized item forms inside the section:
  #   - bullet     ("- foo" / "* foo")
  #   - checkbox   ("- [ ] foo" / "- [x] foo")
  #   - numbered   ("1. foo")
  #   - plain non-empty prose lines (fallback, so a one-line "done" still counts)
  # Returns a [string[]] of trimmed criteria (markup stripped), [] if none.
  param([string] $Text)
  if (-not $Text) { return @() }
  $lines = $Text -split "`r?`n"
  $inSection = $false
  $crit = @()
  $headingRx = '^\s*#{1,6}\s+(.*)$'
  $targetRx  = '^\s*(acceptance\s+criteria|definition\s+of\s+done)\s*$'
  foreach ($ln in $lines) {
    if ($ln -match $headingRx) {
      $title = $Matches[1].Trim()
      if ($title -match $targetRx) { $inSection = $true; continue }
      elseif ($inSection) { break }   # next heading closes the section
      else { continue }
    }
    if (-not $inSection) { continue }
    $t = $ln.Trim()
    if (-not $t) { continue }
    # strip leading list/checkbox/number markup
    $item = $t -replace '^[-*]\s+\[[ xX]\]\s*', '' `
                -replace '^[-*]\s+', '' `
                -replace '^\d+[.)]\s+', ''
    $item = $item.Trim()
    if ($item) { $crit += $item }
  }
  @($crit)
}

function ConvertFrom-VerdictJson {
  # E2 verifier PARSER. Take the raw text a judge subagent returned and produce
  # the exact Protocol `verdict` event object (PROTOCOL §2 field names). The
  # judge is told to emit a JSON object; we tolerate it being wrapped in prose
  # or a ```json fence. Pure — no claude, no I/O. -Item and -Model stamp the
  # engine-known fields; -RawText supplies the judge's body.
  #
  # Accepted judge fields (snake OR camel): pass|verdict, failing_criteria|
  # failingCriteria, evidence, next_action|nextAction. A missing/unparseable
  # body is treated as a FAIL (fail-closed: never let a malformed judge mint a
  # false green). Returns a [pscustomobject] ready to ConvertTo-Json.
  param(
    [string] $RawText,
    [string] $Item,
    [string] $Model
  )
  $pass = $false
  $failing = @()
  $evidence = $null
  $nextAction = $null

  $json = $null
  if ($RawText) {
    # Prefer a fenced ```json block; else the first {...} object in the text.
    $body = $RawText
    if ($RawText -match '(?s)```(?:json)?\s*(\{.*?\})\s*```') { $body = $Matches[1] }
    elseif ($RawText -match '(?s)(\{.*\})') { $body = $Matches[1] }
    try { $json = $body | ConvertFrom-Json -ErrorAction Stop } catch { $json = $null }
  }

  if ($json) {
    # pass: accept bool, or string "pass"/"true"/"yes"
    $passVal = if ($null -ne $json.pass) { $json.pass } else { $json.verdict }
    if ($passVal -is [bool]) { $pass = $passVal }
    elseif ($null -ne $passVal) { $pass = ("$passVal".Trim().ToLower() -in @('true', 'pass', 'passed', 'yes', 'ok')) }

    $fc = if ($null -ne $json.failingCriteria) { $json.failingCriteria } else { $json.failing_criteria }
    if ($fc) { $failing = @($fc | ForEach-Object { "$_" }) }

    if ($null -ne $json.evidence) { $evidence = "$($json.evidence)" }
    $na = if ($null -ne $json.nextAction) { $json.nextAction } else { $json.next_action }
    if ($null -ne $na) { $nextAction = "$na" }
  } else {
    # fail-closed: unparseable judge output cannot certify done.
    $failing = @('verifier output unparseable')
    $evidence = 'judge did not return parseable JSON'
  }

  # A pass with a non-empty failing list is contradictory -> treat as fail.
  if ($pass -and $failing.Count -gt 0) { $pass = $false }

  [pscustomobject]@{
    event           = 'verdict'
    item            = $Item
    pass            = [bool]$pass
    failingCriteria = @($failing)
    evidence        = $evidence
    nextAction      = $nextAction
    model           = $Model
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
    [int]    $MaxIters,
    [bool]   $VerifierRefuted = $false  # gate is green but the verifier refuted "done"
  )

  # Priority order matters. Integrity checks beat success; success beats spend.
  if ($Tampered)     { return [pscustomobject]@{ Action='stop'; Green=$false; Reason='test files were modified (tamper)' } }
  if ($CountDropped) { return [pscustomobject]@{ Action='stop'; Green=$false; Reason='test count dropped (deleted/skipped tests)' } }
  # Anti-false-green: a gate-green that the independent verifier REFUTED is not a
  # real stop-green. Suppress the green-stop and fall through to continue/handoff
  # so the failing criteria get fed back. Default (VerifierRefuted=$false) keeps
  # the original behavior exactly.
  if ($Green -and -not $VerifierRefuted) { return [pscustomobject]@{ Action='stop'; Green=$true;  Reason="all tests green at iter $Iter" } }
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
