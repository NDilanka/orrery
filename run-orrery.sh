#!/usr/bin/env bash
# ============================================================
#  Orrery - launcher (macOS / Linux)
#  Sets up the Python engine, then opens the desktop app wired
#  to it - so you can create / start / stop loops inside the app.
#  (Run:  bash run-orrery.sh   or   chmod +x run-orrery.sh && ./run-orrery.sh)
#
#  Self-test flags (used by run-orrery-tests.sh; documented so the harness
#  can drive the detection logic WITHOUT running the full npm/tauri bootstrap):
#    --check-python  detect the Python interpreter, print its command name,
#                    exit 0; exit 1 (with a message) if none >= 3.10 exists.
#    --check-venv    exit 0 if ./.venv has an importable orrery_loop, else 1.
# ============================================================
set -eu
# cd to the script's own directory using only shell builtins (no `dirname`, so
# the self-test flags run under a stripped PATH of interpreter shims).
case "$0" in
  */*) cd "${0%/*}" ;;
esac

MIN_PY="3.10"

no_python_msg() {
  echo "[!] No suitable Python interpreter found (searched: python3, python; need >= ${MIN_PY}). Install Python ${MIN_PY}+ from https://www.python.org"
}

# Echo the first of python3 / python whose sys.version_info >= (3, 10),
# verified by RUNNING it (not by parsing a version string). Return 1 if none.
detect_python() {
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        echo "$cand"
        return 0
      fi
    fi
  done
  return 1
}

# A venv is healthy ONLY if its interpreter can import the installed engine.
venv_healthy() {
  [ -x ".venv/bin/python" ] && ./.venv/bin/python -c 'import orrery_loop' >/dev/null 2>&1
}

# --- self-test flags (early exit, before the npm check / app launch) ---------
case "${1:-}" in
  --check-python)
    if PY="$(detect_python)"; then echo "$PY"; exit 0; else no_python_msg; exit 1; fi
    ;;
  --check-venv)
    if venv_healthy; then exit 0; else exit 1; fi
    ;;
esac

command -v npm >/dev/null 2>&1 || { echo "[!] Node.js/npm not found - install Node 18+ from https://nodejs.org"; exit 1; }

# 1) Ensure a Python venv with the engine (+ pytest for the seed loop) so the app
#    can spawn the `loop` command when you start a loop. A half-installed venv
#    (interpreter present but engine not importable) is REPAIRED in place; a
#    healthy venv triggers no pip run at all.
if venv_healthy; then
  : # healthy — nothing to install
elif [ -d .venv ]; then
  echo "Repairing the existing Python engine venv (reinstalling the editable engine)..."
  ./.venv/bin/python -m pip install -q -e "engine[dev]"
else
  echo "Setting up the Python engine (first run only)..."
  PY="$(detect_python)" || { no_python_msg; exit 1; }
  "$PY" -m venv .venv
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
