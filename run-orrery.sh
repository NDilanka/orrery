#!/usr/bin/env bash
# ============================================================
#  Orrery - launcher (macOS / Linux)
#  Opens the desktop app from the repo, wired to the engine
#  and your loops. Create / start / stop loops inside the app.
#  (Run:  bash run-orrery.sh   or   chmod +x run-orrery.sh && ./run-orrery.sh)
# ============================================================
set -e
cd "$(dirname "$0")/orrery"

command -v npm >/dev/null 2>&1 || { echo "[!] Node.js/npm not found - install Node 18+ from https://nodejs.org"; exit 1; }

if [ ! -d node_modules ]; then
  echo "Installing dependencies (first run only)..."
  npm install
fi

echo
echo "Launching Orrery desktop app..."
echo " - First launch compiles the Rust core; this can take a few minutes."
echo " - Needs a Rust toolchain (https://rustup.rs) for the desktop build."
echo
npm run tauri dev
