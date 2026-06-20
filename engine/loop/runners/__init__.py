"""Agent-runner registry.

:func:`get_runner` maps a backend name to a concrete :class:`~loop.runners.base.AgentRunner`
instance. ``"claude"``, ``"aider"`` and ``"codex"`` are wired.
"""

from __future__ import annotations

from loop.runners.aider import AiderRunner
from loop.runners.base import AgentResult, AgentRunner, QuotaStatus
from loop.runners.claude import ClaudeRunner
from loop.runners.codex import CodexRunner

__all__ = [
    "AgentResult",
    "AgentRunner",
    "QuotaStatus",
    "ClaudeRunner",
    "AiderRunner",
    "CodexRunner",
    "get_runner",
]

# name -> zero-arg factory. A factory (not a singleton) so each get_runner() call yields a
# fresh, independent runner instance.
_REGISTRY: dict[str, type[AgentRunner]] = {
    "claude": ClaudeRunner,
    "aider": AiderRunner,
    "codex": CodexRunner,
}


def get_runner(name: str) -> AgentRunner:
    """Return a runner instance for ``name`` (e.g. ``"claude"``).

    Raises ``ValueError`` for an unknown backend, naming the ones that are registered.
    """
    factory = _REGISTRY.get(name)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown runner {name!r}; known runners: {known}")
    return factory()
