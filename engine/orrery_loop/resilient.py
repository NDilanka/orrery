"""ResilientRunner — central quota survival around any base runner's ``run``.

Extracted from :mod:`orrery_loop.bmad.driver` so it can be shared by every driver that spawns a
coding agent (the BMAD multi-story pipeline AND the QA discovery pass) instead of each
hand-rolling its own quota wait-loop / token telemetry / raw-output capture. The BMAD driver
re-exports :class:`ResilientRunner` (and :func:`_usage_tokens`) from here for backwards
compatibility, so ``orrery_loop.bmad.driver.ResilientRunner`` keeps resolving.

A :class:`ResilientRunner` wraps a base :class:`~orrery_loop.runners.base.AgentRunner`:

- quota survival: a quota-limited result triggers :func:`orrery_loop.quota.survive` (wait-and-resume)
  and RETRIES the same call once the backend is usable again (resuming the session when the
  backend supports ``--resume`` and the limited attempt carried a session id);
- probe-on-any-error: an errored-but-not-text-flagged result probes the backend ONCE and, if
  that probe is limited, enters the same survive-and-retry path;
- token telemetry: a per-call ``token-usage`` event folded from the result's ``usage`` block;
- raw-output capture: the call's raw stdout persisted to ``run-<phase>[-<story>].out``;
- a liveness :class:`~orrery_loop.heartbeat.Heartbeat` around each call (when an activity dir is set).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from orrery_loop.events import token_usage_event
from orrery_loop.heartbeat import Heartbeat
from orrery_loop.logio import write_run_output
from orrery_loop.quota import survive
from orrery_loop.runners.base import AgentResult, AgentRunner


def _usage_tokens(usage: Any) -> tuple[int, int, int, int]:
    """Pull ``(input, output, cache_read, cache_creation)`` from a claude ``usage`` block.

    Self-contained (mirrors :func:`orrery_loop.cache.get_cache_usage`'s tolerant unwrapping) so the
    telemetry can also surface ``output_tokens`` — which ``CacheUsage`` does not carry — without
    touching the golden-tested ``cache.py``. Accepts a dict, a raw JSON string, or a full result
    object carrying a nested ``usage``; every field defaults to 0 when absent (older claude
    builds / text-format results emit no usage counters), so a result with no telemetry yields
    ``(0, 0, 0, 0)`` and is skipped by the caller.
    """
    u: Any = usage
    if isinstance(u, str):
        try:
            u = json.loads(u)
        except (ValueError, TypeError):
            u = None
    if isinstance(u, dict) and u.get("usage"):
        u = u["usage"]

    def g(*names: str) -> int:
        if not isinstance(u, dict):
            return 0
        for n in names:
            v = u.get(n)
            if v is None:
                continue
            try:
                return int(float(v))
            except (TypeError, ValueError):
                continue
        return 0

    return (
        g("input_tokens", "inputTokens"),
        g("output_tokens", "outputTokens"),
        g("cache_read_input_tokens", "cacheReadInputTokens"),
        g("cache_creation_input_tokens", "cacheCreationInputTokens"),
    )


class ResilientRunner(AgentRunner):
    """Quota-survival adapter wrapping a base runner (port of ``Invoke-ResilientClaude``).

    Implements :meth:`AgentRunner.run` by delegating to ``base_runner.run(...)``; when the
    result is quota-limited it calls :func:`orrery_loop.quota.survive` (the wait-and-resume driver) and
    RETRIES the same call once survival reports the backend is usable again. Because EVERY phase
    receives THIS wrapped runner, quota survival is centralized — no phase needs its own
    wait-loop. Capability flags + ``probe_quota`` / ``map_model`` delegate to the base so the
    phases' ``--resume`` threading still keys off the real backend's ``supports_sessions``.
    """

    def __init__(
        self,
        base_runner: AgentRunner,
        *,
        emit: Callable[[dict[str, Any]], None],
        quota_cfg: dict[str, Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        activity_path: Any = None,
        fallback_model: str = "",
    ):
        self._base = base_runner
        self._emit = emit
        self._sleep = sleep
        # Wave-4 Task A — a comma-separated model chain the CLI falls back to on overload. Injected
        # (when non-empty) into EVERY wrapped call so all BMAD/QA phases + deciders + verify/plan-gate
        # inherit overload resilience in ONE place. Empty (default) -> never injected -> the wrapped
        # call is byte-identical to before (parity), and non-claude base runners accept-and-ignore it.
        self._fallback_model = str(fallback_model or "")
        # When set, a liveness Heartbeat overwrites this file (activity.json) every few seconds
        # for the DURATION of each agent call, so a watcher can tell a long silent phase (a 30-min
        # dev-story is one continuous claude call that emits no log line until its gate) from a
        # hung loop. None disables it (the mock-runner tests don't need it).
        self._activity_path = activity_path
        cfg = quota_cfg or {}
        self._default_wait_min = int(cfg.get("default_wait_min", 30))
        self._max_waits = int(cfg.get("max_waits", 30))
        self._cum = float(cfg.get("cum", 0.0))
        # Phase/story context + cumulative TOKEN draw — the REAL Max-plan meter (USD ``cum`` is
        # meaningless on a subscription). ``set_context`` is called by the driver before each
        # phase so every ``token-usage`` event is tagged with which phase + story spent the
        # tokens. Cache *reads* are tracked separately because they barely count against the rate
        # budget, so a high ``cumCacheRead`` relative to ``cumInput`` is the GOOD sign.
        self._phase = "bmad-phase"
        self._story: str | None = None
        self._cum_input = 0
        self._cum_output = 0
        self._cum_cache_read = 0
        self._cum_cache_creation = 0
        # Mirror the base backend's capabilities so phases branch identically.
        self.name = getattr(base_runner, "name", "resilient")
        self.supports_quota_probe = getattr(base_runner, "supports_quota_probe", False)
        self.supports_sessions = getattr(base_runner, "supports_sessions", False)
        self.supports_cache_telemetry = getattr(
            base_runner, "supports_cache_telemetry", False
        )

    def set_cum(self, cum: float) -> None:
        """Update the running cumulative cost the quota-hit/wait events report."""
        self._cum = float(cum)

    def set_context(self, phase: str, story: str | None = None) -> None:
        """Tag subsequent runs' ``token-usage`` telemetry with the current phase + story.

        Called by the driver at each phase boundary. Deciders invoked inside a phase inherit
        that phase's label (they are part of its cost); their ``haiku`` ``model`` tag still
        distinguishes them within the phase. A no-op for token accounting itself.
        """
        self._phase = phase
        self._story = story

    def _record_usage(self, res: AgentResult, model: str) -> None:
        """Emit a per-call ``token-usage`` event from the result's ``usage`` block.

        Tokens are the Max-plan meter (USD is not), and the data is already in claude's
        ``--output-format json`` response — so this costs ZERO extra tokens, it just stops
        throwing the numbers away. Results with no usage telemetry (mock runners / text-format)
        carry all-zero counters and emit nothing, keeping non-claude tests' logs clean.
        """
        inp, out, cr, cc = _usage_tokens(getattr(res, "usage", None))
        if not (inp or out or cr or cc):
            return
        self._cum_input += inp
        self._cum_output += out
        self._cum_cache_read += cr
        self._cum_cache_creation += cc
        denom = cr + inp
        hit_ratio = (cr / float(denom)) if denom else 0.0
        self._emit(
            token_usage_event(
                phase=self._phase,
                story=self._story,
                model=str(model or ""),
                input_tokens=inp,
                output_tokens=out,
                cache_read=cr,
                cache_creation=cc,
                hit_ratio=hit_ratio,
                warm=cr > 0,
                cost_usd=float(getattr(res, "cost_usd", 0.0) or 0.0),
                cum_input=self._cum_input,
                cum_output=self._cum_output,
                cum_cache_read=self._cum_cache_read,
                cum_cache_creation=self._cum_cache_creation,
            )
        )

    def _write_raw_capture(self, res: AgentResult) -> None:
        """Persist this call's raw stdout to ``.loop/run-<phase>[-<story>].out``.

        A debugging artifact for postmortem on a failed/halted phase — previously ``res.raw`` was
        thrown away entirely. Named per phase/story (NOT per call) so it is overwritten on every
        retry/attempt within that phase+story — the LATEST raw output is what matters, and this
        keeps the file count bounded across a whole multi-story run. Lives alongside
        ``activity.json`` (same state dir); a no-op when no activity dir is configured (the
        mock-runner tests that don't set ``activity_path``) or ``raw`` is empty.
        """
        if self._activity_path is None:
            return
        raw = getattr(res, "raw", None)
        if not raw:
            return
        name = f"run-{self._phase}-{self._story}.out" if self._story else f"run-{self._phase}.out"
        write_run_output(Path(self._activity_path).parent / name, raw)

    def _invoke(self, call_kwargs: dict[str, Any]) -> AgentResult:
        """One base-runner call, wrapped in the liveness Heartbeat for the call's lifetime.

        `cwd` is the repo whose dirty-file count is the "actually producing work" signal. The
        heartbeat is scoped to the call (not the quota wait — that surfaces via quota events).
        """
        if self._activity_path is not None:
            with Heartbeat(
                self._activity_path,
                phase=self._phase,
                story=self._story,
                repo=call_kwargs.get("cwd"),
            ):
                return self._base.run(**call_kwargs)
        return self._base.run(**call_kwargs)

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        label = "bmad-phase"
        # FIX 4: after surviving a quota hit that carried a session id, RESUME that session on the
        # retry instead of re-running the whole (possibly 40-min Opus) phase from scratch. `resume`
        # holds the session id to add to the NEXT attempt; `fresh_fallback_used` caps the recovery
        # at exactly ONE non-resume fallback if a resumed attempt errors non-quota (never loops).
        resume: str | None = None
        fresh_fallback_used = False
        while True:
            call_kwargs = dict(kwargs)
            # Inject the fallback-model chain only when configured AND the caller didn't already set
            # one, so an unset config leaves the call byte-identical (fixed-signature test doubles
            # never see an unexpected kwarg).
            if self._fallback_model and "fallback_model" not in call_kwargs:
                call_kwargs["fallback_model"] = self._fallback_model
            if resume:
                call_kwargs["resume_session"] = resume
            was_resume = resume is not None
            resume = None

            res = self._invoke(call_kwargs)
            self._write_raw_capture(res)

            quota_limited = bool(getattr(res, "quota_limited", False))
            # FIX 3: the battle-tested PS loop ran an independent quota probe on ANY failed phase,
            # not just result-TEXT-flagged ones. When a result ERRORED but wasn't text-flagged as
            # quota-limited, probe ONCE; a limited probe enters the same survive-and-retry path. A
            # clean probe leaves the error handling unchanged. Guarded to at most one probe per
            # failed attempt, and only for backends that actually probe (no probe on success).
            if (
                not quota_limited
                and getattr(res, "is_error", False)
                and getattr(self._base, "supports_quota_probe", False)
                and getattr(self._base.probe_quota(), "limited", False)
            ):
                quota_limited = True

            if not quota_limited:
                # FIX 4 fallback: a RESUMED attempt that failed with a NON-quota error gets exactly
                # ONE fresh (non-resume) attempt, then gives up as today (no infinite retry).
                if was_resume and getattr(res, "is_error", False) and not fresh_fallback_used:
                    fresh_fallback_used = True
                    continue
                self._record_usage(res, kwargs.get("model", ""))
                return res

            recovered = survive(
                self._base,
                label=label,
                cum=self._cum,
                emit=self._emit,
                sleep=self._sleep,
                default_wait_min=self._default_wait_min,
                max_waits=self._max_waits,
            )
            if not recovered:
                # Give up — surface the quota-limited result so the phase stops.
                return res
            # recovered -> retry. If this quota-limited attempt carried a session id (and the
            # backend supports --resume), continue the SAME session; else re-run the call.
            sid = getattr(res, "session_id", None)
            if sid and getattr(self._base, "supports_sessions", False):
                resume = sid

    def probe_quota(self):
        return self._base.probe_quota()

    def map_model(self, tier: str) -> str:
        return self._base.map_model(tier)
