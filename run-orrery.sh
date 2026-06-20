#!/usr/bin/env bash
# ============================================================
#  Orrery - launcher (macOS / Linux)
#  Sets up the Python engine, then opens the desktop app wired
#  to it - so you can create / start / stop loops inside the app.
#  (Run:  bash run-orrery.sh   or   chmod +x run-orrery.sh && ./run-orrery.sh)
# ============================================================
set -e
cd "$(dirname "$0")"

command -v npm >/dev/null 2>&1 || { echo "[!] Node.js/npm not found - install Node 18+ from https://nodejs.org"; exit 1; }

# 1) Ensure a Python venv with the engine (+ pytest for the seed loop) so the app
#    can spawn the `loop` command when you start a loop.
if [ ! -x ".venv/bin/loop" ]; then
  echo "Setting up the Python engine (first run only)..."
  command -v python3 >/dev/null 2>&1 || { echo "[!] Python 3.10+ not found"; exit 1; }
  python3 -m venv .venv
  ./.venv/bin/python -m pip install -q -e "engine[dev]"
fi

# 2) Put the engine + its tools on PATH so the app's spawned loop/pytest resolve.
export PATH="$(pwd)/.venv/bin:$PATH"

# 3) UI deps + launch the desktop app.
cd orrery
if [ ! -d node_modules ]; then
  echo "Installing UI dependencies (first run only)..."
  npm install
fi

echo
echo "Launching Orrery desktop app..."
echo " - First launch compiles the Rust core; this can take a few minutes."
echo " - Needs a Rust toolchain (https://rustup.rs) for the desktop build."
echo
npm run tauri dev
