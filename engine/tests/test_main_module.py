"""Module entry-point test — ``python -m orrery_loop --help``.

Exercises ``engine/orrery_loop/__main__.py`` through a real subprocess (not by importing
``main`` directly) so it proves the ``-m`` invocation the gate uses actually resolves and
exits 0 with the standard usage text. This is a NEW file; no existing test is modified.
"""

from __future__ import annotations

import subprocess
import sys


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
