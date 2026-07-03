"""ClaudeRunner — a faithful port of the ``claude -p`` invocation in ``loop.ps1``.

The two side-effecting paths the PowerShell harness isolates so it can stub them are both
here, and both go through :mod:`loop.proc` (the ONE place a real ``claude`` process spawns):

- :meth:`ClaudeRunner.run` ports the execute call (``loop.ps1`` ~635): build the exact argv,
  run it, parse the ``--output-format json`` result object into an :class:`AgentResult`.
- :meth:`ClaudeRunner.probe_quota` ports ``Invoke-QuotaProbe`` (``loop.ps1`` ~209): a cheap
  ``stream-json`` probe whose combined stdout+stderr is handed to the PURE
  :func:`loop.quota.resolve_quota_status` parser.

Tests monkeypatch this module's ``proc`` reference so no real ``claude`` is ever spawned.
"""

from __future__ import annotations

import json

from loop import proc, quota
from loop.runners.base import AgentResult, AgentRunner, QuotaStatus


class ClaudeRunner(AgentRunner):
    """The ``claude`` CLI backend. Full capabilities: quota probe, sessions, cache telemetry."""

    name = "claude"
    supports_quota_probe = True
    supports_sessions = True
    supports_cache_telemetry = True

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
        mcp_config: str = "",
        strict_mcp_config: bool = False,
        fallback_model: str = "",
        json_schema: str = "",
        settings: str = "",
    ) -> AgentResult:
        # Build argv EXACTLY like loop.ps1 (~635): a single --allowedTools flag followed by
        # the tool list spread as separate positional args (PS: '--allowedTools' + $toolArgs).
        #
        # An empty/None/"default"/"inherit" model OMITS --model entirely so the agent inherits
        # the user's Claude Code default model. This is faithful to bmad-loop.ps1, which never
        # passed --model. A real tier (e.g. "sonnet"/"opus"/"haiku") is passed through as-is.
        argv = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            output_format,
            "--max-turns",
            str(max_turns),
        ]
        normalized = str(model).strip() if model is not None else ""
        if normalized and normalized.lower() not in {"default", "inherit"}:
            argv += ["--model", model]
        # --effort (verified `claude` CLI flag, levels low|medium|high|xhigh|max). Empty / None /
        # 'default' / 'inherit' OMITS it so the agent inherits the user's Claude Code effort
        # default — byte-identical to the pre-effort argv when unset.
        normalized_effort = str(effort).strip() if effort is not None else ""
        if normalized_effort and normalized_effort.lower() not in {"default", "inherit"}:
            argv += ["--effort", normalized_effort]
        argv += ["--permission-mode", permission_mode]
        # Optional per-run MCP config (the QA discovery pass loads ONLY a pre-authenticated
        # Playwright server this way). Empty mcp_config OMITS both flags, so the argv is
        # byte-identical to the pre-MCP form for every existing caller (BMAD / generic loop).
        if mcp_config:
            argv += ["--mcp-config", mcp_config]
            if strict_mcp_config:
                argv.append("--strict-mcp-config")
        # --- Wave-4 opt-in flags (all default "" -> omitted, byte-identical argv when unset) ----
        # --fallback-model takes ONE value that may itself be a comma-separated chain the CLI tries
        # in order (verified against claude 2.1.199 --help: "Accepts a comma-separated list"). We
        # pass it through verbatim so a caller can hand us "sonnet,haiku" as a single token.
        if fallback_model:
            argv += ["--fallback-model", fallback_model]
        # --json-schema takes the schema INLINE as a JSON string (verified: the CLI example is a
        # literal '{"type":"object",...}' arg, not a file path). Pass it straight through.
        if json_schema:
            argv += ["--json-schema", json_schema]
        # --settings takes a file path OR inline JSON. The in-session gate writes a settings file
        # and hands us its path here (Wave-4 Task C, experimental).
        if settings:
            argv += ["--settings", settings]
        argv += ["--allowedTools", *list(allowed_tools)]
        if resume_session:
            argv += ["--resume", resume_session]

        res = proc.run_with_timeout(argv, cwd=cwd, timeout_sec=timeout_sec)

        # A killed (hung) claude is a non-productive run: error + timed_out, no parse attempt.
        if res.timed_out:
            return AgentResult(raw=res.stdout or "", is_error=True, timed_out=True)

        raw = res.stdout or ""
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            # Unparseable stdout — same verdict the PS loop reaches ($parsed -eq $null ->
            # callFailed). Flag parse_failed so the driver can emit parse_error + stop even when
            # raw is a non-empty garbage string (loop.ps1:662).
            return AgentResult(raw=raw, is_error=True, parse_failed=True)

        if not isinstance(parsed, dict):
            return AgentResult(raw=raw, is_error=True, parse_failed=True)

        is_error = bool(parsed.get("is_error", False))
        # --json-schema validated output rides on the result JSON's `structured_output` field
        # (confirmed via the SDK structured-outputs docs). Only surface a dict; anything else
        # (absent field on a normal run, or a non-object) leaves `structured` None so consumers
        # fall back to text parsing. A run that requested a schema but failed validation reports
        # subtype `error_max_structured_output_retries` with no structured_output -> None here.
        so = parsed.get("structured_output")
        structured = so if isinstance(so, dict) else None
        return AgentResult(
            raw=raw,
            text=parsed.get("result", "") or "",
            cost_usd=float(parsed.get("total_cost_usd", 0.0) or 0.0),
            is_error=is_error,
            session_id=parsed.get("session_id"),
            usage=parsed.get("usage"),
            quota_limited=is_error and quota.test_quota_limited_text(raw),
            structured=structured,
        )

    def probe_quota(self) -> QuotaStatus:
        # Port of Invoke-QuotaProbe (loop.ps1 ~209): a cheap stream-json probe; hand the
        # combined stdout+stderr to the pure resolver.
        argv = [
            "claude",
            "-p",
            "ok",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "1",
        ]
        # FINITE probe timeout (120s): a hung probe would otherwise defeat survive()'s <=6h
        # auto-resume guarantee (an unbounded probe can block the wait loop forever).
        res = proc.run_with_timeout(argv, cwd=None, timeout_sec=120)
        if res.timed_out:
            # A hung/killed probe is INCONCLUSIVE — treat it as "still limited" so survive() sleeps
            # a cycle and re-probes next iteration (bounded by max_waits) instead of hanging or
            # falsely reporting "available". No reset moment is known, so the wait falls back to
            # the default interval.
            return QuotaStatus(limited=True, reset_at=None, reset_type=None)
        combined = (res.stdout or "") + "\n" + (res.stderr or "")
        status = quota.resolve_quota_status(combined)
        return QuotaStatus(
            limited=status.limited,
            reset_at=status.reset_at,
            reset_type=status.reset_type,
        )
