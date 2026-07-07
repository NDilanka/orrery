"""Module entry point so ``python -m orrery_loop`` works when the ``loop`` script name is shadowed.

Delegates to :func:`orrery_loop.cli.main` — the same callable the ``loop`` console script uses.
"""

from __future__ import annotations

from orrery_loop.cli import main

if __name__ == "__main__":
    # Propagate main()'s return code as the process exit status — mirrors the generated
    # `loop` console script (`sys.exit(main())`). A bare `main()` would swallow the code
    # and always exit 0, a false green for any exit-code-driven caller (CI gate,
    # loop-supervise, `$?`), and would drop the 130 that main() returns on Ctrl-C.
    raise SystemExit(main())
