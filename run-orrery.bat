@echo off
REM ============================================================
REM  Orrery - double-click launcher (Windows)
REM  Opens the desktop app from the repo, wired to the engine
REM  and your loops. Create / start / stop loops inside the app.
REM ============================================================
setlocal
cd /d "%~dp0orrery"

where npm >nul 2>nul
if errorlevel 1 (
  echo [!] Node.js / npm not found. Install Node 18+ from https://nodejs.org and retry.
  pause
  exit /b 1
)

if not exist node_modules (
  echo Installing dependencies ^(first run only^)...
  call npm install
)

echo.
echo Launching Orrery desktop app...
echo  - First launch compiles the Rust core; this can take a few minutes.
echo  - A desktop window will open. Fly Cosmos -^> a System; hit the ignite
echo    button to author a loop, or start/stop the seeded roman/calc loops.
echo  - Needs a Rust toolchain (https://rustup.rs) for the desktop build.
echo.

call npm run tauri dev

REM keep the window open if the launch failed so you can read the error
if errorlevel 1 pause
endlocal
