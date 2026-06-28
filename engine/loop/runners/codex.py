"""CodexRunner — wraps the OpenAI Codex CLI (``codex exec``) as an AgentRunner.

Best-effort backend: the Codex CLI surface is still evolving, so flags and output shape may
need adjustment over time (noted at the argv build site). Like :mod:`loop.runners.aider` this
is a thin text-mode subprocess wrapper — one non-interactive turn normalized into an
:class:`AgentResult`.

Quota handling: Codex 429s are surfaced reactively. ``base.AgentResult`` has no ``reset_at``
field, so even when the rate-limit response carries a ``Retry-After: <n>`` we do NOT try to
thread an exact wait through the result — we just set ``quota_limited=True`` and let the loop's
``survive()`` non-probing fallback do the waiting (CodexRunner has ``supports_quota_probe =
False``, so there's no precise probe path to feed anyway). The detected Retry-After seconds are
parsed only to confirm the 429 and noted in a debug-friendly way; the wait itself is the
fallback's default.

Spawning goes through :mod:`loop.proc` exactly like :mod:`loop.runners.claude`; tests
monkeypatch this module's ``proc`` reference so no real ``codex`` is ever launched.
"""

from __future__ import annotations

import re

from loop import proc, quota
from loop.runners.base import AgentResult, AgentRunner

# Codex/`litellm`-style cost or token lines, best-effort. We accept a "Cost: $X.XX" form (same
# convention aider uses) and a bare "total_cost": <num> JSON-ish field if one slips through in
# text mode. Absent any such line, cost is 0.0.
_COST_RX = re.compile(
    r"(?:Cost:\s*\$|\"total_cost(?:_usd)?\"\s*:\s*\$?)\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

# Error markers Codex may print on a failed turn even with a near-zero exit.
_ERROR_RX = re.compile(
    r"^\s*(?:error|fatal|exception)\b|traceback \(most recent call last\)|"
    r"\bcodex:\s*error\b|\"error\"\s*:",
    re.IGNORECASE | re.MULTILINE,
)

# A `Retry-After: <seconds>` header echoed in the 429 body/text. Parsed only to confirm the
# rate-limit (the value itself is not threaded into AgentResult — see module docstring).
_RETRY_AFTER_RX = re.compile(r"retry[-_ ]?after\s*[:=]?\s*(\d+)", re.IGNORECASE)


class CodexRunner(AgentRunner):
    """The OpenAI Codex CLI backend. No quota probe, no sessions, no cache telemetry."""

    name = "codex"
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
    ) -> AgentResult:
        # NOTE: the Codex CLI surface is evolving; these flags ('exec', '--full-auto',
        # '--model') may need adjustment as the CLI stabilizes. We parse TEXT output here, so
        # we deliberately do NOT pass '--json' (would only be added together with JSON parsing).
        # allowed_tools / permission_mode / resume_session / max_turns / output_format / effort
        # have no Codex equivalent we drive — accepted and ignored on purpose.
        argv = [
            "codex",
            "exec",
            prompt,
            "--full-auto",
            "--model",
            self.map_model(model),
        ]

        res = proc.run_with_timeout(argv, cwd=cwd, timeout_sec=timeout_sec)

        if res.timed_out:
            return AgentResult(raw=res.stdout or "", is_error=True, timed_out=True)

        raw = res.stdout or ""
        stderr = res.stderr or ""
        combined = raw + "\n" + stderr

        # Reactive rate-limit detection: the strong-phrase matcher already covers HTTP 429 and
        # "rate-limit"/"too many requests"; a bare Retry-After also confirms a throttle.
        quota_limited = quota.test_quota_limited_text(combined) or bool(
            _RETRY_AFTER_RX.search(combined)
        )

        # Text output is EXPECTED (not JSON) → parse_failed stays False.
        is_error = res.returncode != 0 or bool(_ERROR_RX.search(combined))

        return AgentResult(
            raw=raw,
            text=raw,
            cost_usd=self._parse_cost(combined),
            is_error=is_error,
            quota_limited=quota_limited,
            parse_failed=False,
        )

    @staticmethod
    def _parse_cost(text: str) -> float:
        """Extract a USD cost from any ``Cost: $X.XX`` / ``"total_cost": X`` line.

        Rule: take the LAST matching figure (the run's cumulative total if Codex prints a
        running tally), else 0.0 when no cost line is present.
        """
        matches = _COST_RX.findall(text)
        if matches:
            try:
                return float(matches[-1])
            except ValueError:
                return 0.0
        return 0.0

    def map_model(self, tier: str) -> str:
        """Map a logical tier to an OpenAI model id.

        ``haiku`` / ``sonnet`` / ``opus`` map to a small / mid / large OpenAI model
        respectively (sensible defaults for a cheap / balanced / strong tier). Any OTHER string
        is passed through unchanged so a caller can hand Codex a concrete model id verbatim.
        """
        return _MODEL_MAP.get(tier, tier)


# Tier -> OpenAI model id (cheap / balanced / strong). Defaults; override by passing a concrete
# model id, which is passed through unchanged.
_MODEL_MAP = {
    "haiku": "gpt-4o-mini",
    "sonnet": "gpt-4o",
    "opus": "gpt-4.1",
}
