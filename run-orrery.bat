@echo off
REM ============================================================
REM  Orrery - double-click launcher (Windows)
REM  Sets up the Python engine, then opens the desktop app wired
REM  to it - so you can create / start / stop loops inside the app.
REM ============================================================
setlocal
cd /d "%~dp0"

where npm >nul 2>nul
if errorlevel 1 (
  echo [!] Node.js / npm not found. Install Node 18+ from https://nodejs.org and retry.
  pause
  exit /b 1
)

REM 1) Ensure a Python venv with the engine (+ pytest for the seed loop) so the app
REM    can spawn the `loop` command when you start a loop.
if not exist ".venv\Scripts\loop.exe" (
  echo Setting up the Python engine ^(first run only^)...
  where py >nul 2>nul && ( py -3 -m venv .venv ) || ( python -m venv .venv )
  if errorlevel 1 ( echo [!] Could not create a Python venv. Install Python 3.10+ and retry. & pause & exit /b 1 )
  call ".venv\Scripts\python.exe" -m pip install -q -e "engine[dev]"
  if errorlevel 1 ( echo [!] Engine install failed. & pause & exit /b 1 )
)

REM 2) Put the engine + its tools on PATH so the app's spawned `loop`/`pytest` resolve.
set "PATH=%~dp0.venv\Scripts;%PATH%"

REM 3) UI deps + launch the desktop app.
cd orrery
if not exist node_modules (
  echo Installing UI dependencies ^(first run only^)...
  call npm install
)

echo.
echo Launching Orrery desktop app...
echo  - First launch compiles the Rust core; this can take a few minutes.
echo  - In the window: hit the ignite button to author a loop, or start the
echo    seeded "hello" loop to watch the engine fix a bug live.
echo  - Needs a Rust toolchain (https://rustup.rs) for the desktop build.
echo.

call npm run tauri dev

if errorlevel 1 pause
endlocal
