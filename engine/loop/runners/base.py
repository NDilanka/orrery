"""The pluggable agent-runner interface — the EXACT contract other engine code depends on.

An :class:`AgentRunner` abstracts "run one coding-agent turn and tell me what happened" so
the loop driver is independent of which backend (claude / aider / codex …) actually does the
work. The first concrete backend is :class:`loop.runners.claude.ClaudeRunner`.

``AgentResult`` is the normalized return of a single run; ``QuotaStatus`` is the normalized
return of a quota probe (a thin echo of :class:`loop.quota.QuotaStatus` so callers don't have
to depend on the quota module's frozen dataclass). Capability flags let the driver branch on
what a backend can do (quota probing, ``--resume`` sessions, cache telemetry) instead of
hardcoding claude behavior.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AgentResult:
    """Normalized result of a single agent run.

    ``raw`` is the backend's untouched stdout. ``text`` is the human-readable result body
    (claude's ``result`` field). ``is_error`` is True on a failed/parse-failed/timed-out run.
    ``quota_limited`` is a hint the driver can use to decide whether to invoke quota survival
    (the authoritative answer still comes from :meth:`AgentRunner.probe_quota`).
    ``parse_failed`` is True when the backend's stdout did NOT parse to JSON (mirrors
    ``loop.ps1``'s ``$parsed -eq $null`` branch) — even when ``raw`` is a non-empty garbage
    string. It is NOT set on a clean success or a timeout (a timeout is its own path).
    """

    raw: str
    text: str = ""
    cost_usd: float = 0.0
    is_error: bool = False
    session_id: str | None = None
    usage: dict | None = None
    timed_out: bool = False
    quota_limited: bool = False
    parse_failed: bool = False


@dataclass
class QuotaStatus:
    """Normalized result of a quota probe — limited? and (if known) when/what resets."""

    limited: bool
    reset_at: object | None = None  # datetime or None
    reset_type: str | None = None  # 'five_hour' | 'weekly' | None


class AgentRunner(ABC):
    """Abstract base every concrete backend implements.

    Capability flags default to False (the conservative null backend); a backend sets the
    ones it actually supports. The base :meth:`probe_quota` reports "not limited" and the base
    :meth:`map_model` is identity, so a minimal backend only has to implement :meth:`run`.
    """

    name: str
    supports_quota_probe: bool = False
    supports_sessions: bool = False
    supports_cache_telemetry: bool = False

    @abstractmethod
    def run(
        self,
        *,
        prompt: str,
        model: str,
        allowed_tools,
        permission_mode: str,
        max_turns: int,
        cwd,
        timeout_sec: int = 0,
        resume_session: str | None = None,
        output_format: str = "json",
        effort: str = "",
    ) -> AgentResult:
        """Run one agent turn and return a normalized :class:`AgentResult`.

        ``effort`` is the reasoning-effort tier (``low``/``medium``/``high``/``xhigh``/``max``);
        an empty string INHERITS the backend's default (the backend omits any effort flag).
        Backends that have no effort knob accept-and-ignore it.
        """
        ...

    def probe_quota(self) -> QuotaStatus:
        """Cheaply test whether the backend is currently quota-limited.

        Default: backends that don't support probing always report "available".
        """
        return QuotaStatus(limited=False)

    def map_model(self, tier: str) -> str:
        """Map a logical model tier ('plan'/'execute'/…) to a backend model id.

        Default: identity — the tier string IS the model id.
        """
        return tier
