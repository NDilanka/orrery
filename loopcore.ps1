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

# ===========================================================================
# E3: QUOTA SURVIVAL — pure, testable cores.
#
# The authoritative probe (`claude -p "ok" --output-format stream-json …`) and
# the real Start-Sleep live in ISOLATED wrappers in loop.ps1 (Invoke-QuotaProbe
# / Start-QuotaSleep) so tests can stub them. Everything decidable WITHOUT
# claude or a real sleep lives here and is unit-tested by selftest-resume.ps1.
# ===========================================================================

function Resolve-QuotaStatus {
  # PURE parser. Given the combined stdout+stderr text of a stream-json probe,
  # decide whether quota is LIMITED and, if so, which reset moment to sleep to.
  #
  # Reads every `"rate_limit_info": { … }` fragment (the machine-readable signal
  # claude emits in stream-json). A fragment is "rejected" when its status is
  # reject/block/exceed/limited. The sleep target prefers the REJECTED window's
  # resetsAt (which may be the five_hour OR the weekly one — so a WEEKLY limit
  # waits for the weekly reset instead of giving up), else the five_hour reset.
  # rateLimitType (five_hour|weekly) of the chosen reset is returned as ResetType.
  #
  # Returns @{ Limited=<bool>; ResetAt=<datetime|null>; ResetType=<str|null> }.
  # No I/O, no claude, no sleep — fully deterministic for a given text.
  param([string] $Text)

  $all = if ($null -ne $Text) { ($Text -replace "\x1b\[[0-9;]*m", "") } else { "" }
  $limited = $false
  $rejectReset = $null;   $rejectType = $null
  $fiveHourReset = $null

  foreach ($m in [regex]::Matches($all, '"rate_limit_info"\s*:\s*\{[^}]*\}')) {
    $frag = $m.Value
    $st = if ($frag -match '"status"\s*:\s*"([^"]+)"') { $Matches[1] } else { '' }
    $rs = if ($frag -match '"resetsAt"\s*:\s*(\d+)') { [DateTimeOffset]::FromUnixTimeSeconds([long]$Matches[1]).LocalDateTime } else { $null }
    $rt = if ($frag -match '"rateLimitType"\s*:\s*"([^"]+)"') { $Matches[1] } else { $null }
    if ($st -imatch 'reject|block|exceed|limited') {
      $limited = $true
      if ($rs) { $rejectReset = $rs; $rejectType = $rt }
    }
    if ($rt -eq 'five_hour' -and $rs) { $fiveHourReset = $rs }
  }

  # Fallback: hard limit phrasing in the text (HTTP 429/529, "usage limit", …)
  # still flags limited even when no rate_limit_info fragment is present.
  if (-not $limited -and (Test-QuotaLimitedText -Text $all)) { $limited = $true }

  if ($rejectReset) {
    [pscustomobject]@{ Limited = $limited; ResetAt = $rejectReset; ResetType = $rejectType }
  } elseif ($fiveHourReset) {
    [pscustomobject]@{ Limited = $limited; ResetAt = $fiveHourReset; ResetType = 'five_hour' }
  } else {
    [pscustomobject]@{ Limited = $limited; ResetAt = $null; ResetType = $null }
  }
}

function Test-QuotaLimitedText {
  # PURE. STRONG limit phrases only, so ordinary content / max_turns errors don't
  # trip it. Matches real limit text + the API error type, but NOT the benign
  # 'rate_limit_info'/'rate_limit_event' stream-json fields.
  param([string] $Text)
  if (-not $Text) { return $false }
  $strong = 'usage limit|limit will reset|reset[s]?\s+(at|in)\b|reset[s]?\s+\d|\d\s*-?\s*hour limit|too many requests|\b429\b|overloaded|\b529\b|rate[ -]limit|rate_limit_error'
  return [bool]($Text -imatch $strong)
}

function Get-QuotaWaitSec {
  # PURE. Compute the seconds to sleep for one wait cycle. When a concrete reset
  # moment is known, sleep until reset + a 120s buffer, clamped to [60, 21600]
  # (1m..6h — a single cycle never sleeps more than 6h; repeated cycles cover a
  # weekly reset). When no reset is known, fall back to DefaultWaitMin minutes.
  # -Now is injectable so tests are deterministic (no real clock).
  param(
    [Nullable[datetime]] $ResetAt,
    [int] $DefaultWaitMin = 30,
    [datetime] $Now = (Get-Date),
    [int] $BufferSec = 120,
    [int] $MinSec = 60,
    [int] $MaxSec = 21600
  )
  if ($ResetAt) {
    $sec = ([int](($ResetAt - $Now).TotalSeconds)) + $BufferSec
    return [int][math]::Min([math]::Max($sec, $MinSec), $MaxSec)
  }
  return ($DefaultWaitMin * 60)
}

function New-QuotaWaitEvent {
  # PURE. Build the exact PROTOCOL §2 `quota-wait` event object. resumeAt is the
  # ISO-8601 moment the loop will re-probe (Now + waitSec). resetType is omitted
  # (left $null) when unknown so ConvertTo-Json drops/keeps it per PROTOCOL.
  param(
    [string] $Label, [double] $Cum, [int] $WaitSec, [int] $Probe,
    [string] $ResetType = $null, [datetime] $Now = (Get-Date)
  )
  [pscustomobject]@{
    event     = 'quota-wait'
    label     = $Label
    cum       = $Cum
    waitSec   = $WaitSec
    resumeAt  = $Now.AddSeconds($WaitSec).ToString('o')
    probe     = $Probe
    resetType = $ResetType
  }
}

function New-QuotaHitEvent {
  # PURE. Build the PROTOCOL §2 `quota-hit` event object.
  param([string] $Label, [double] $Cum, [Nullable[datetime]] $ResetAt = $null)
  [pscustomobject]@{
    event   = 'quota-hit'
    label   = $Label
    cum     = $Cum
    resetAt = $(if ($ResetAt) { ([datetime]$ResetAt).ToString('o') } else { $null })
  }
}

function New-QuotaResumeEvent {
  # PURE. Build the PROTOCOL §2 `quota-resume` event object.
  param([string] $Label, [int] $Probe)
  [pscustomobject]@{ event = 'quota-resume'; label = $Label; probe = $Probe }
}

# ===========================================================================
# E3: COOPERATIVE SAFE-STOP + CHECKPOINT RESUME — pure cores.
# ===========================================================================

function Get-StopMode {
  # PURE. Given the raw contents of a STOP flag file (or $null when absent),
  # normalize to the requested mode. Empty/whitespace -> 'phase' (the default,
  # matching stop-loop.ps1). Recognized: phase|story|now. -Scope is the boundary
  # we are AT ('phase' = a within-iteration commit boundary; 'story'/'now' map to
  # the between-iteration boundary for the generic single-goal loop).
  #
  # Returns @{ Requested=<str|null>; Honor=<bool>; Mode=<str|null> } where Honor
  # is whether a stop at THIS scope should fire. A 'story' request is held until a
  # 'story' (between-iteration) scope; 'phase'/'now' fire at any scope.
  param(
    [object] $FlagContent,
    [string] $Scope = 'story'
  )
  if ($null -eq $FlagContent) { return [pscustomobject]@{ Requested = $null; Honor = $false; Mode = $null } }
  $mode = ("" + $FlagContent).Trim().ToLower()
  if ($mode -eq '') { $mode = 'phase' }
  if ($mode -notin @('phase', 'story', 'now')) { $mode = 'phase' }
  # 'story' request waits for a story (between-iteration) boundary.
  $honor = -not ($Scope -eq 'phase' -and $mode -eq 'story')
  [pscustomobject]@{ Requested = $mode; Honor = $honor; Mode = $mode }
}

function New-Checkpoint {
  # PURE. Build the EXACT PROTOCOL §7 checkpoint.json object (ordered for stable
  # round-trip). resume = the literal shell command string to continue the loop.
  # No I/O — loop.ps1 serializes the returned object to <StateDir>/checkpoint.json.
  param(
    [string] $Stage,
    [object] $Story,
    [string] $Branch,
    [string] $MergeBase,
    [double] $CumUsd,
    [string] $Resume,
    [datetime] $UpdatedAt = (Get-Date)
  )
  [ordered]@{
    updatedAt = $UpdatedAt.ToString('o')
    stage     = $Stage
    story     = $Story
    branch    = $Branch
    mergeBase = $MergeBase
    cumUsd    = [math]::Round($CumUsd, 4)
    resume    = $Resume
  }
}

function New-CooperativeStopEvent {
  # PURE. Build the PROTOCOL §2 `cooperative-stop` event object.
  param(
    [string] $Scope, [string] $Mode, [string] $Stage,
    [object] $Story, [string] $Branch, [double] $Cum
  )
  [pscustomobject]@{
    event  = 'cooperative-stop'
    scope  = $Scope
    mode   = $Mode
    stage  = $Stage
    story  = $Story
    branch = $Branch
    cum    = $Cum
  }
}

# ===========================================================================
# E3: REGRESSION ROLLBACK STRIKES — pure event builders.
# ===========================================================================

function New-RollbackEvent {
  # PURE. Build the PROTOCOL §2 `rollback` event. strike = the rollback number
  # just taken (1-based); strikeBudget = the RegressLimit. item = the goal/task.
  param(
    [string] $Item, [int] $ToIter, [int] $BestPass, [int] $Strike, [int] $StrikeBudget
  )
  [pscustomobject]@{
    event        = 'rollback'
    item         = $Item
    toIter       = $ToIter
    bestPass     = $BestPass
    strike       = $Strike
    strikeBudget = $StrikeBudget
  }
}

function New-HandoffEvent {
  # PURE. Build the PROTOCOL §2 `handoff` event raised when strikes are exhausted.
  param([string] $Item, [string] $Reason, [int] $Consecutive)
  [pscustomobject]@{
    event       = 'handoff'
    item        = $Item
    reason      = $Reason
    consecutive = $Consecutive
  }
}
