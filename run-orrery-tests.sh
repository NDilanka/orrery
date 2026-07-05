#!/usr/bin/env bash
# ============================================================
#  Tests for run-orrery.sh's bootstrap logic (GitHub #11, #12).
#
#  These are BEHAVIORAL tests, not grep-the-script assertions: each scenario
#  builds a sandbox directory with fake interpreter shims on a controlled PATH
#  (or a fake .venv) and drives run-orrery.sh's own --check-python / --check-venv
#  self-test flags through it, asserting the real detection / health-check code.
#
#  Portable to git-bash on Windows: uses `command -v`, POSIX shell builtins, and
#  shell-script shims (chmod +x). No GNU-only tools.
# ============================================================
set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$REPO_DIR/run-orrery.sh"
BASH_BIN="$(command -v bash)"

# Temp root for all sandboxes; cleaned on exit.
TMP_ROOT="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/orrery-tests.$$")"
mkdir -p "$TMP_ROOT"
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

FAILS=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }

# Write a fake python-interpreter shim reporting version $2.$3 to path $1.
# It answers the exact `sys.version_info >= (3, 10)` probe run-orrery.sh uses.
make_py_shim() {
  shim="$1"; maj="$2"; min="$3"
  mkdir -p "$(dirname "$shim")"
  cat > "$shim" <<EOF
#!/bin/sh
# fake python interpreter reporting version ${maj}.${min}
case "\$*" in
  *version_info*)
    if [ ${maj} -gt 3 ]; then exit 0; fi
    if [ ${maj} -eq 3 ] && [ ${min} -ge 10 ]; then exit 0; fi
    exit 1
    ;;
esac
exit 0
EOF
  chmod +x "$shim"
}

# Write a fake venv interpreter at $1/.venv/bin/python; $2=yes/no for whether
# `import orrery_loop` succeeds.
make_venv() {
  sandbox="$1"; importable="$2"
  shim="$sandbox/.venv/bin/python"
  mkdir -p "$(dirname "$shim")"
  code=1
  [ "$importable" = "yes" ] && code=0
  cat > "$shim" <<EOF
#!/bin/sh
# fake venv python; import orrery_loop -> exit ${code}
case "\$*" in
  *"import orrery_loop"*) exit ${code} ;;
esac
exit 0
EOF
  chmod +x "$shim"
}

# Run a copy of run-orrery.sh in a sandbox under a controlled interpreter PATH.
# $1=sandbox dir, $2=PATH to use, rest=args. Prints nothing; sets RC and OUT.
run_script() {
  sandbox="$1"; use_path="$2"; shift 2
  cp "$SCRIPT" "$sandbox/run-orrery.sh"
  OUT="$( ( PATH="$use_path"; "$BASH_BIN" "$sandbox/run-orrery.sh" "$@" ) 2>/dev/null )"
  RC=$?
}

# ---- --check-python: interpreter fallback + version gate -------------------

# 1) python3 present & new, python absent -> chooses python3.
s="$TMP_ROOT/s1"; mkdir -p "$s/bin"
make_py_shim "$s/bin/python3" 3 11
run_script "$s" "$s/bin" --check-python
if [ "$RC" -eq 0 ] && [ "$OUT" = "python3" ]; then pass "python3 chosen when present and >=3.10"; else fail "python3 chosen (rc=$RC out=$OUT)"; fi

# 2) python3 absent, python present & new -> falls back to python.
s="$TMP_ROOT/s2"; mkdir -p "$s/bin"
make_py_shim "$s/bin/python" 3 12
run_script "$s" "$s/bin" --check-python
if [ "$RC" -eq 0 ] && [ "$OUT" = "python" ]; then pass "python fallback when no python3"; else fail "python fallback (rc=$RC out=$OUT)"; fi

# 3) python3 present but OLD, python present & new -> skips old python3, picks python.
s="$TMP_ROOT/s3"; mkdir -p "$s/bin"
make_py_shim "$s/bin/python3" 3 9
make_py_shim "$s/bin/python" 3 10
run_script "$s" "$s/bin" --check-python
if [ "$RC" -eq 0 ] && [ "$OUT" = "python" ]; then pass "old python3 rejected, python chosen"; else fail "old python3 rejected (rc=$RC out=$OUT)"; fi

# 4) both present but OLD -> no suitable interpreter.
s="$TMP_ROOT/s4"; mkdir -p "$s/bin"
make_py_shim "$s/bin/python3" 3 9
make_py_shim "$s/bin/python" 2 7
run_script "$s" "$s/bin" --check-python
if [ "$RC" -ne 0 ]; then pass "both interpreters too old -> non-zero"; else fail "both too old (rc=$RC out=$OUT)"; fi

# 5) no interpreter at all -> non-zero.
s="$TMP_ROOT/s5"; mkdir -p "$s/bin"
run_script "$s" "$s/bin" --check-python
if [ "$RC" -ne 0 ]; then pass "no interpreter -> non-zero"; else fail "no interpreter (rc=$RC out=$OUT)"; fi

# ---- --check-venv: engine-import health check -----------------------------

# 6) healthy venv (orrery_loop importable) -> exit 0.
s="$TMP_ROOT/s6"; mkdir -p "$s/bin"
make_venv "$s" yes
run_script "$s" "$s/bin" --check-venv
if [ "$RC" -eq 0 ]; then pass "healthy venv -> exit 0"; else fail "healthy venv (rc=$RC)"; fi

# 7) unhealthy venv (import fails) -> exit 1.
s="$TMP_ROOT/s7"; mkdir -p "$s/bin"
make_venv "$s" no
run_script "$s" "$s/bin" --check-venv
if [ "$RC" -ne 0 ]; then pass "unhealthy venv -> non-zero"; else fail "unhealthy venv (rc=$RC)"; fi

# 8) no venv interpreter present -> exit 1.
s="$TMP_ROOT/s8"; mkdir -p "$s/bin"
run_script "$s" "$s/bin" --check-venv
if [ "$RC" -ne 0 ]; then pass "missing venv -> non-zero"; else fail "missing venv (rc=$RC)"; fi

echo "----"
if [ "$FAILS" -eq 0 ]; then
  echo "ALL SCENARIOS PASSED"
  exit 0
else
  echo "$FAILS SCENARIO(S) FAILED"
  exit 1
fi
