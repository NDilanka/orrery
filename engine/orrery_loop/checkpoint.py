"""Cooperative safe-stop core — port of ``Get-StopMode`` (loopcore.ps1 ~510-531).

Pure, no I/O. The caller reads the STOP flag file's contents (or passes ``None`` when the
flag is absent) and the boundary ``scope`` it is currently AT; this normalizes the request
and decides whether a stop should fire at that scope.
"""

from __future__ import annotations

from typing import Any

_MODES = ("phase", "story", "now")


def get_stop_mode(flag_content: Any | None, scope: str = "story") -> dict[str, Any]:
    """Port of ``Get-StopMode``.

    Given the raw contents of a STOP flag file (or ``None`` when absent), normalize to the
    requested mode and decide whether a stop at ``scope`` should fire.

    - ``None`` content -> ``{requested: None, honor: False, mode: None}``.
    - Empty / whitespace -> ``'phase'`` (the default).
    - Unrecognized value -> ``'phase'``.
    - A ``'story'`` request is HELD (``honor=False``) when ``scope == 'phase'`` (it waits
      for a between-iteration boundary); ``'phase'`` / ``'now'`` fire at any scope.

    Returns ``{requested, honor, mode}``.
    """
    if flag_content is None:
        return {"requested": None, "honor": False, "mode": None}
    mode = ("" + str(flag_content)).strip().lower()
    if mode == "":
        mode = "phase"
    if mode not in _MODES:
        mode = "phase"
    # 'story' request waits for a story (between-iteration) boundary.
    honor = not (scope == "phase" and mode == "story")
    return {"requested": mode, "honor": honor, "mode": mode}
