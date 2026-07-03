# supervise.ps1 — keep the BMAD loop alive with the CORRECT (--loop-json) config.
#
# Why: the checkpoint `resume` string (used by a plain restart / orrery "Reignite") does NOT
# carry --loop-json, so a restart silently drops back to the default engine config and re-hits
# the review cap. This supervisor always relaunches with the intended engine config.
#
# Customize the C:\path\to\... placeholders below before use.
#
# Stop it cleanly:  New-Item -ItemType File "C:\path\to\loop-repo\orrery\loops\bmad\.loop\STOP-SUPERVISOR"
#                   then (optionally)  loop-stop --state-dir <dir> --now
# Status:           Get-Content "C:\path\to\loop-repo\orrery\loops\bmad\.loop\supervisor.log" -Tail 20

$ErrorActionPreference = 'Continue'
$stateDir = 'C:\path\to\loop-repo\orrery\loops\bmad\.loop'
$exe      = 'C:\path\to\loop-repo\.venv\Scripts\loop-bmad.exe'
$loopJson = 'C:/path/to/loop-repo/orrery/loops/bmad/bmad-engine.json'
$loopArgs = @(
    '--project-root', 'C:/path/to/your-project',
    '--state-dir',    $stateDir,
    '--merge-base',   'develop',
    '--loop-json',    $loopJson,
    '--no-smoke'      # optional: skip the browser-smoke phase so the loop progresses unattended,
                      # gated by your project's codegen/lint/test stages instead.
)
$log      = Join-Path $stateDir 'supervisor.log'
$sentinel = Join-Path $stateDir 'STOP-SUPERVISOR'
$logFile  = Join-Path $stateDir 'log.jsonl'
$stopFlag = Join-Path $stateDir 'STOP'
$runOut   = Join-Path $stateDir 'run.out'
$runErr   = Join-Path $stateDir 'run.err'
$restarts = New-Object System.Collections.Generic.Queue[datetime]

function Log($m) { "$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))  $m" | Add-Content -LiteralPath $log }

Log "supervisor started (pid $PID) — keeping loop alive with --loop-json"
while ($true) {
    if (Test-Path $sentinel) { Log 'STOP-SUPERVISOR present -> exiting, no relaunch.'; break }

    $proc = Get-Process -Name 'loop-bmad' -ErrorAction SilentlyContinue
    if ($proc) { Start-Sleep -Seconds 30; continue }

    # loop process is DOWN — decide whether to relaunch
    $tail = ''
    if (Test-Path $logFile) { $tail = (Get-Content -LiteralPath $logFile -Tail 4 -ErrorAction SilentlyContinue) -join "`n" }
    if ($tail -match 'backlog complete') { Log 'loop ended: backlog complete -> exiting supervisor (nothing left to do).'; break }

    # thrash guard: at most 5 relaunches per rolling 90 min. A HEALTHY loop runs through many
    # stories in ONE process (0 relaunches), so relaunches == stops; 5 stops in 90 min => a real
    # failure (e.g. smoke that can't pass) — stop relaunching and leave it for a human.
    $now = [DateTime]::Now
    while ($restarts.Count -gt 0 -and ($now - $restarts.Peek()).TotalMinutes -gt 90) { [void]$restarts.Dequeue() }
    if ($restarts.Count -ge 5) { Log "thrash guard: 5 relaunches in 90 min -> exiting. Investigate $logFile (something is failing repeatedly)."; break }

    # cause it recovered from (best-effort, for the user)
    $reason = ($tail -split "`n" | Where-Object { $_ -match '"reason"|stop|halt' } | Select-Object -Last 1)
    if (-not $reason) { $reason = '(no stop reason in log tail; process exited)' }

    # clear any stale STOP flag so the fresh instance doesn't immediately honor an old --after-story
    if (Test-Path $stopFlag) { Remove-Item -LiteralPath $stopFlag -Force -ErrorAction SilentlyContinue }

    Log "loop DOWN -> relaunching with --loop-json. recovered-from: $reason"
    Start-Process -FilePath $exe -ArgumentList $loopArgs -WindowStyle Hidden `
        -RedirectStandardOutput $runOut -RedirectStandardError $runErr
    $restarts.Enqueue($now)
    Start-Sleep -Seconds 45   # give it time to acquire the lock + spin up before re-checking
}
Log 'supervisor exited.'
