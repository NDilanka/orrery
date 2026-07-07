"""Module entry-point test — ``python -m orrery_loop --help``.

Exercises ``engine/orrery_loop/__main__.py`` through a real subprocess (not by importing
``main`` directly) so it proves the ``-m`` invocation the gate uses actually resolves and
exits 0 with the standard usage text. This is a NEW file; no existing test is modified.
"""

from __future__ import annotations

import runpy
import subprocess
import sys

import pytest

import orrery_loop.cli as cli


def test_module_entry_help_exits_0_with_usage():
    proc = subprocess.run(
        [sys.executable, "-m", "orrery_loop", "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # Same usage surface as the ``loop`` console script (argparse prog is "loop").
    assert "usage: loop" in proc.stdout
    assert "fix-until-green loop" in proc.stdout


def test_module_entry_propagates_nonzero_exit_code(monkeypatch):
    """``python -m orrery_loop`` must exit with ``main()``'s return code, not swallow it.

    ``main()`` returns ``1`` when the loop never goes green (``core.run_loop``) and ``130``
    on Ctrl-C — the generated ``loop`` console script propagates those via ``sys.exit(main())``.
    A bare ``main()`` in ``__main__.py`` would return the code but exit 0, a false green for any
    exit-code-driven caller (CI gate, ``loop-supervise``, ``$?``). Patch ``cli.main`` to return a
    nonzero sentinel and assert the module raises ``SystemExit`` carrying it. (Exercises the
    ``-m`` entry via ``runpy`` in-process; a real red loop would need a live runner.)
    """
    monkeypatch.setattr(cli, "main", lambda argv=None: 7)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("orrery_loop", run_name="__main__", alter_sys=True)
    # The int code is preserved verbatim (not coerced to None/0) — this is what a bare
    # ``main()`` call would fail to do.
    assert excinfo.value.code == 7
