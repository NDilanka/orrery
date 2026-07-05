"""Module entry point so ``python -m orrery_loop`` works when the ``loop`` script name is shadowed.

Delegates to :func:`orrery_loop.cli.main` — the same callable the ``loop`` console script uses.
"""

from __future__ import annotations

from orrery_loop.cli import main

if __name__ == "__main__":
    main()
