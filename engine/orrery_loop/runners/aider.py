"""AiderRunner — wraps the third-party ``aider`` coding-agent CLI as an AgentRunner.

``aider`` is a plain text-mode CLI (no JSON result envelope), so this backend is a thin
subprocess wrapper that runs ONE non-interactive message turn and normalizes the text output
into an :class:`AgentResult`.

Design notes / contract deviations (all intentional, all because aider has no equivalent):

- The loop owns git, so we pass ``--no-auto-commit`` — aider must NOT make its own commits.
- ``--yes`` makes the single ``--message`` turn fully non-interactive (no confirm prompts).
- ``allowed_tools`` / ``permission_mode`` / ``resume_session`` have NO aider equivalent and are
  accepted-but-ignored (documented here so the no-op is deliberate, not an oversight).
- Capability flags are ALL False: aider can't cheaply probe quota, has no ``--resume`` session
  model we drive, and exposes no cache telemetry. So :meth:`probe_quota` stays the base no-op
  ("available"), and the driver's quota survival uses the reactive non-probing fallback.

Spawning goes through :mod:`orrery_loop.proc` exactly like :mod:`orrery_loop.runners.claude`; tests
monkeypatch this module's ``proc`` reference so no real ``aider`` is ever launched.
"""

from __future__ import annotations

import re

from orrery_loop import proc, quota
from orrery_loop.runners.base import AgentResult, AgentRunner

# aider prints, per turn, lines like:
#   Tokens: 1.2k sent, 350 received. Cost: $0.0123 message, $0.0456 session.
# We capture the "$X.XX session" form (cumulative) when present, else the "$X.XX message" /
# bare "$X.XX" form. Dollar amounts may carry a trailing 'k'/'m'? — aider only emits plain
# dollars, so we match a plain decimal. We tolerate surrounding text via a forgiving regex.
_COST_SESSION_RX = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*session", re.IGNORECASE)
_COST_ANY_RX = re.compile(r"Cost:\s*\$\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)

# Generic error markers aider may print on a failed turn even with a zero-ish exit. Kept
# conservative so ordinary output ("no errors found") doesn't trip it: we look for explicit
# error-prefixed lines, tracebacks, and aider's own fatal phrasing.
_ERROR_RX = re.compile(
    r"^\s*(?:error|fatal|exception)\b|traceback \(most recent call last\)|"
    r"\baider:\s*error\b|litellm\.\w*error",
    re.IGNORECASE | re.MULTILINE,
)


class AiderRunner(AgentRunner):
    """The ``aider`` CLI backend. No quota probe, no sessions, no cache telemetry."""

    name = "aider"
    supports_quota_probe = False
    supports_sessions = False
    supports_cache_telemetry = False

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
        fallback_model: str = "",
        json_schema: str = "",
        settings: str = "",
    ) -> AgentResult:
        # allowed_tools / permission_mode / resume_session / max_turns / output_format / effort /
        # fallback_model / json_schema / settings have no aider equivalent — accepted and ignored
        # on purpose (see module docstring), exactly like `effort`.
        argv = [
            "aider",
            "--message",
            prompt,
            "--yes",
            "--no-auto-commit",
            "--model",
            self.map_model(model),
        ]

        res = proc.run_with_timeout(argv, cwd=cwd, timeout_sec=timeout_sec)

        # A killed (hung) aider is a non-productive run: error + timed_out, no parse attempt —
        # same shape as ClaudeRunner's timeout path.
        if res.timed_out:
            return AgentResult(raw=res.stdout or "", is_error=True, timed_out=True)

        raw = res.stdout or ""
        stderr = res.stderr or ""
        combined = raw + "\n" + stderr

        # aider emits text, not JSON — text output is EXPECTED, so parse_failed stays False.
        is_error = res.returncode != 0 or bool(_ERROR_RX.search(combined))

        return AgentResult(
            raw=raw,
            text=raw,
            cost_usd=self._parse_cost(combined),
            is_error=is_error,
            # Reactive 429 / rate-limit detection over the combined streams (no probe path).
            quota_limited=quota.test_quota_limited_text(combined),
            parse_failed=False,
        )

    @staticmethod
    def _parse_cost(text: str) -> float:
        """Extract a USD cost from aider's ``Cost: $X.XX ...`` lines.

        Rule: prefer the LAST ``$X.XX session`` figure (aider's cumulative per-run total, so
        the final occurrence is the run's full cost); else fall back to the LAST ``Cost: $X.XX``
        figure on any line; else 0.0. Taking the last occurrence avoids summing per-message
        lines into a double count, since the "session" number already accumulates them.
        """
        session = _COST_SESSION_RX.findall(text)
        if session:
            try:
                return float(session[-1])
            except ValueError:
                return 0.0
        any_cost = _COST_ANY_RX.findall(text)
        if any_cost:
            try:
                return float(any_cost[-1])
            except ValueError:
                return 0.0
        return 0.0

    def map_model(self, tier: str) -> str:
        """Map a logical tier to an aider model string.

        ``haiku`` / ``sonnet`` / ``opus`` map to reasonable Anthropic model ids aider accepts
        (aider routes Anthropic ids straight through to the Anthropic API). Any OTHER string is
        passed through unchanged, so a caller can hand aider a fully-qualified model id (e.g.
        ``"openai/gpt-4o"`` or ``"claude-3-5-sonnet-20241022"``) verbatim.
        """
        return _MODEL_MAP.get(tier, tier)


# Tier -> aider model id. Anthropic ids; aider passes these to the Anthropic provider.
_MODEL_MAP = {
    "haiku": "claude-3-5-haiku-latest",
    "sonnet": "claude-3-5-sonnet-latest",
    "opus": "claude-3-opus-latest",
}
