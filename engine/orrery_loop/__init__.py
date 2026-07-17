"""orrery-loop: pure, testable harness logic for the Orrery loop engine.

A faithful Python port of the PowerShell ``loopcore.ps1`` decision/event cores and the
core ``log.jsonl`` event shapes from ``orrery/PROTOCOL.md`` §2. No network, no claude
calls, no I/O — every function is deterministic and unit-tested for parity against the
authoritative PowerShell source.
"""

__version__ = "0.5.0"
