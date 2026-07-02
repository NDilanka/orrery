"""QA discovery driver — exercise a running app against its acceptance criteria.

One *bounded* agent session per epic: the agent drives a headless, pre-authenticated
Playwright-MCP browser through the epic's screens, judges each browser-observable AC
(pass / fail / partial / blocked / not-observable) with evidence, authors a functional
Playwright spec, and writes a machine-readable findings file. The driver maps those
findings onto the Orrery event stream (PROTOCOL §2) and aggregates a ``findings.json``
+ ``report.md`` across all epics.

Design mirrors the rest of the engine: the side-effecting bits — spawning ``claude`` and
appending events — are INJECTED (``invoke`` / ``emit``), so the orchestration is unit-testable
with a fake invoker and no real browser. ``default_invoke`` / ``default_emit`` wire the real
``claude -p`` (with a per-run ``--mcp-config`` so the Playwright server loads the saved auth)
and ``log.jsonl`` writer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loop import gitutil, proc
from loop.heartbeat import Heartbeat
from loop.logio import append_event, read_stop_flag, write_checkpoint

# Verdict statuses the agent assigns each AC. Only the first four are "observable" and
# therefore counted in a story's pass-rate gate; NOT_OBSERVABLE drops out of the denominator.
_PASS = "pass"
_FAILING = {"fail", "partial", "blocked"}
_NOT_OBSERVABLE = "not-observable"

# Default browser tool surface + file tools (read ACs/seed, author the spec, write findings).
DEFAULT_ALLOWED_TOOLS = ("mcp__playwright", "Read", "Write", "Edit", "Glob", "Grep")


@dataclass
class QaConfig:
    """All inputs for a QA discovery run. The app under test is pure configuration."""

    project_root: str  # repo the agent runs in (writes specs here); also the git/branch source
    manifest_path: str  # ac-manifest.json (built by loop.qa.manifest)
    base_url: str = "http://localhost:3000"
    app: str = "app"
    spec_dir: str = "e2e/functional"  # relative to project_root
    storage_state: str = ""  # abs path to the Playwright auth storageState (auth-bootstrap output)
    seed_summary: str = ""  # short description of the seeded fixture (the data oracle)
    model: str = ""  # "" / "default" / "inherit" -> agent's default model
    effort: str = ""  # "" inherits; e.g. "high"
    max_turns: int = 120
    timeout_sec: int = 1800  # per-epic wall-clock (30 min)
    cost_ceiling_usd: float | None = None
    epics: list[int] | None = None  # None -> all epics in the manifest
    headless: bool = True
    caps: str = "devtools"
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS
    permission_mode: str = "acceptEdits"

    @staticmethod
    def from_loop_json(data: dict, *, project_root: str | None = None) -> "QaConfig":
        q = dict(data.get("qa", data) or {})
        pr = project_root or q.get("projectRoot") or q.get("project_root") or "."
        return QaConfig(
            project_root=pr,
            manifest_path=q.get("manifest", q.get("manifestPath", "ac-manifest.json")),
            base_url=q.get("baseUrl", "http://localhost:3000"),
            app=q.get("app", "app"),
            spec_dir=q.get("specDir", "e2e/functional"),
            storage_state=q.get("storageState", ""),
            seed_summary=q.get("seedSummary", ""),
            model=q.get("model", ""),
            effort=q.get("effort", ""),
            max_turns=int(q.get("maxTurns", 120)),
            timeout_sec=int(q.get("timeoutSec", 1800)),
            cost_ceiling_usd=q.get("costCeilingUsd"),
            epics=q.get("epics"),
            headless=bool(q.get("headless", True)),
            caps=q.get("caps", "devtools"),
        )


@dataclass
class InvokeResult:
    """Normalized return of one agent session (subset of runners.AgentResult)."""

    raw: str
    text: str = ""
    cost_usd: float = 0.0
    is_error: bool = False
    timed_out: bool = False


# --------------------------------------------------------------------------------------
# Pure helpers (no IO) — unit-testable on their own.
# --------------------------------------------------------------------------------------

def story_gate(verdicts: list[dict]) -> dict:
    """Reduce a story's per-AC verdicts to a gate: observable ACs met vs not.

    ``total`` counts only observable ACs (NOT_OBSERVABLE drops out of the denominator);
    ``pass`` are the met ones; a story is ``green`` when it has observable ACs and none fail.
    """
    observable = [v for v in verdicts if (v.get("status") or "").lower() != _NOT_OBSERVABLE]
    passed = [v for v in observable if (v.get("status") or "").lower() == _PASS]
    failing = [v for v in observable if (v.get("status") or "").lower() in _FAILING]
    total = len(observable)
    return {
        "pass": len(passed),
        "fail": len(failing),
        "total": total,
        "green": total > 0 and len(failing) == 0,
        "failingCriteria": [v.get("ac", "?") for v in failing],
    }


def build_epic_prompt(
    epic: dict,
    *,
    base_url: str,
    spec_rel_path: str,
    findings_abs_path: str,
    seed_summary: str,
) -> str:
    """Compose the discovery prompt for one epic from its stories' AC markdown."""
    stories_md = []
    for s in epic["stories"]:
        stories_md.append(
            f"### Story {s['id']} — {s['title']}  (status: {s['status']})\n\n{s['acMarkdown']}"
        )
    stories_block = "\n\n---\n\n".join(stories_md)
    n = epic["epic"]
    seed_block = (
        f"\nThe test database is SEEDED with a fixed fixture (your data oracle):\n{seed_summary}\n"
        if seed_summary
        else ""
    )
    findings_json = findings_abs_path.replace("\\", "/")
    return f"""You are a senior QA engineer running a FUNCTIONAL acceptance pass on a live web app
(epic {n}) through a real headless browser. The browser is already SIGNED IN (a saved auth
session is loaded) — start by navigating to {base_url} and confirming you are authenticated;
if you land on a sign-in page, record that as a BLOCKING finding and stop.

Use the `mcp__playwright__*` browser tools to drive the app. You may also use the devtools
capability to inspect console errors and failed network requests as evidence. Be SKEPTICAL:
verify each criterion by actually exercising it in the UI — never assume it passes because the
story says "done".
{seed_block}
## Acceptance criteria to verify (epic {n})

{stories_block}

## What to do

1. For EACH acceptance criterion above, decide whether it is **browser-observable** (something a
   user can see/do in the running UI). Backend-only clauses (mutation shapes, scheduling, server
   indexes, unit-test assertions) are `not-observable` — record them as such, do not fail them.
2. For each observable criterion, exercise it in the app and judge:
   `pass` (fully met) · `partial` (partly met) · `fail` (not met) · `blocked` (couldn't reach it).
   Capture concrete EVIDENCE: what you saw, the accessible name/selector, any console/network
   error, or a screenshot snapshot reference.
3. Author a Playwright regression spec at `{spec_rel_path}` covering the observable behaviors you
   confirmed. Mirror the existing `e2e/*.e2e.ts` conventions: `import {{ test, expect }} from
   "@playwright/test"` and `import {{ signInTestUser }} from "../utils/auth"`; sign in at the top
   of each test; prefer role/label selectors (`getByRole`, `getByLabel`). The file MUST end in
   `.e2e.ts` so the existing suite picks it up. Only assert behaviors you actually verified.

## Required output (machine-readable — this is the deliverable)

As your FINAL action, use the Write tool to write EXACTLY this JSON to the absolute path
`{findings_json}` (create parent dirs):

{{
  "epic": {n},
  "summary": "<2-3 sentence overall health of this epic>",
  "specFile": "{spec_rel_path}",
  "verdicts": [
    {{ "story": "<e.g. {epic['stories'][0]['id']}>", "ac": "<e.g. AC1>",
       "observable": true,
       "status": "pass|partial|fail|blocked|not-observable",
       "evidence": "<what you saw / selector / console error>",
       "severity": "none|low|medium|high|critical",
       "repro": "<steps to reproduce, only when status is fail/partial/blocked>",
       "specWritten": true }}
  ]
}}

Include one verdict object for EVERY AC listed above (every `**ACn**` of every story). Keep
`evidence` concise. Do not omit ACs. Do not wrap the JSON in markdown fences.
"""


def findings_to_events(epic_num: int, findings: dict, *, cum: float, iter_index: int) -> list[dict]:
    """Map an epic's findings file to Orrery events (PROTOCOL §2 core + qa.* extensions)."""
    events: list[dict] = []
    verdicts = findings.get("verdicts", []) or []

    # Group verdicts by story, preserving first-seen order.
    by_story: dict[str, list[dict]] = {}
    for v in verdicts:
        by_story.setdefault(str(v.get("story", "?")), []).append(v)

    epic_pass = epic_fail = epic_total = 0
    for story_id, vs in by_story.items():
        gate = story_gate(vs)
        epic_pass += gate["pass"]
        epic_fail += gate["fail"]
        epic_total += gate["total"]
        events.append(
            {
                "event": "verdict",
                "item": story_id,
                "pass": gate["green"],
                "failingCriteria": gate["failingCriteria"],
                "evidence": (vs[0].get("evidence", "") if vs else "")[:280],
                "model": "qa-discovery",
            }
        )
        # Per-AC detail for the live LOG panel (reducer ignores unknown events).
        for v in vs:
            events.append(
                {
                    "event": "qa-ac",
                    "epic": epic_num,
                    "story": story_id,
                    "ac": v.get("ac"),
                    "status": v.get("status"),
                    "severity": v.get("severity", "none"),
                    "evidence": (v.get("evidence", "") or "")[:280],
                }
            )

    events.append(
        {
            "event": "gate",
            "story": f"epic-{epic_num}",
            "cum": round(cum, 4),
            "green": epic_fail == 0 and epic_total > 0,
            "pass": epic_pass,
            "fail": epic_fail,
            "total": epic_total,
            "baselinePass": 0,
        }
    )
    events.append(
        {
            "event": "iter",
            "iter": iter_index,
            "cost": 0.0,
            "cum": round(cum, 4),
            "pass": epic_pass,
            "total": epic_total,
            "best": epic_pass,
            "changed": True,
            "stale": 0,
            "plateau": 0,
            "regress": 0,
            "action": "continue",
            "reason": f"epic {epic_num}: {epic_pass}/{epic_total} observable ACs met",
        }
    )
    return events


# --------------------------------------------------------------------------------------
# Default IO wiring (the real claude + the real log writer).
# --------------------------------------------------------------------------------------

def write_mcp_config(path, *, storage_state: str, headless: bool, caps: str) -> None:
    """Write a per-run --mcp-config that loads ONLY the Playwright server with saved auth."""
    args = ["-y", "@playwright/mcp@latest"]
    if headless:
        args.append("--headless")
    args.append("--isolated")
    if caps:
        args += ["--caps", caps]
    if storage_state:
        args += ["--storage-state", storage_state.replace("\\", "/")]
    cfg = {"mcpServers": {"playwright": {"command": "npx", "args": args}}}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def default_invoke(config: QaConfig, mcp_config_path: str):
    """Build the real ``claude -p`` invoker bound to ``config`` + a per-run mcp config."""

    def _invoke(prompt: str, *, timeout_sec: int) -> InvokeResult:
        argv = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--max-turns",
            str(config.max_turns),
        ]
        m = str(config.model).strip()
        if m and m.lower() not in {"default", "inherit"}:
            argv += ["--model", m]
        e = str(config.effort).strip()
        if e and e.lower() not in {"default", "inherit"}:
            argv += ["--effort", e]
        argv += [
            "--permission-mode",
            config.permission_mode,
            "--mcp-config",
            mcp_config_path,
            "--strict-mcp-config",
            "--allowedTools",
            *config.allowed_tools,
        ]
        res = proc.run_with_timeout(argv, cwd=config.project_root, timeout_sec=timeout_sec)
        if res.timed_out:
            return InvokeResult(raw=res.stdout or "", is_error=True, timed_out=True)
        try:
            parsed = json.loads(res.stdout or "")
        except (json.JSONDecodeError, ValueError, TypeError):
            return InvokeResult(raw=res.stdout or "", is_error=True)
        if not isinstance(parsed, dict):
            return InvokeResult(raw=res.stdout or "", is_error=True)
        return InvokeResult(
            raw=res.stdout or "",
            text=parsed.get("result", "") or "",
            cost_usd=float(parsed.get("total_cost_usd", 0.0) or 0.0),
            is_error=bool(parsed.get("is_error", False)),
        )

    return _invoke


def default_emit(log_path) -> Callable[[dict], None]:
    def _emit(event: dict) -> None:
        append_event(log_path, event)

    return _emit


# --------------------------------------------------------------------------------------
# Orchestration.
# --------------------------------------------------------------------------------------

def _render_report(app: str, branch: str, epic_results: list[dict]) -> str:
    lines = [f"# QA discovery report — {app}", "", f"Branch: `{branch}`", ""]
    total_obs = total_pass = total_fail = 0
    for er in epic_results:
        g = er["gate"]
        total_obs += g["total"]
        total_pass += g["pass"]
        total_fail += g["fail"]
    lines.append(
        f"**Overall:** {total_pass}/{total_obs} observable ACs met across "
        f"{len(epic_results)} epics · {total_fail} not met."
    )
    lines.append("")
    for er in epic_results:
        g = er["gate"]
        mark = "✅" if g["green"] else "⚠️"
        lines.append(f"## {mark} Epic {er['epic']} — {g['pass']}/{g['total']} met")
        if er.get("summary"):
            lines.append("")
            lines.append(er["summary"])
        fails = [v for v in er.get("verdicts", []) if (v.get("status") or "").lower() in _FAILING]
        if fails:
            lines.append("")
            lines.append("| Story | AC | Status | Severity | Evidence |")
            lines.append("|---|---|---|---|---|")
            for v in fails:
                ev = (v.get("evidence", "") or "").replace("|", "\\|")[:120]
                lines.append(
                    f"| {v.get('story')} | {v.get('ac')} | {v.get('status')} "
                    f"| {v.get('severity', '')} | {ev} |"
                )
        lines.append("")
    return "\n".join(lines)


def run(
    config: QaConfig,
    *,
    state_dir: str,
    invoke: Callable[..., InvokeResult] | None = None,
    emit: Callable[[dict], None] | None = None,
) -> int:
    """Run the discovery pass across the configured epics. Returns a process exit code."""
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    log_path = state / "log.jsonl"
    activity_path = state / "activity.json"
    stop_flag = state / "STOP"
    findings_dir = state / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)

    emit = emit or default_emit(log_path)

    manifest = json.loads(Path(config.manifest_path).read_text(encoding="utf-8"))
    epics = manifest.get("epics", [])
    if config.epics is not None:
        wanted = set(config.epics)
        epics = [e for e in epics if e["epic"] in wanted]

    branch = gitutil.current_branch(config.project_root) or "?"
    emit({"event": "engine-start", "mergeBase": branch})
    emit(
        {
            "event": "start",
            "target": f"{config.app} QA — {len(epics)} epics",
            "branch": branch,
            "baselinePass": 0,
        }
    )
    emit({"event": "model", "phase": "qa-discovery", "model": "opus"})

    cum = 0.0
    epic_results: list[dict] = []
    stop_reason = "all epics complete"

    for i, epic in enumerate(epics, start=1):
        if read_stop_flag(stop_flag) is not None:
            stop_reason = "cooperative stop"
            emit({"event": "cooperative-stop", "scope": "phase", "stage": f"epic-{epic['epic']}",
                  "story": None, "branch": branch, "cum": round(cum, 4)})
            break

        n = epic["epic"]
        spec_rel = f"{config.spec_dir}/epic-{n}.func.e2e.ts"
        findings_path = findings_dir / f"epic-{n}.json"
        # Stale-result guard: a previous run's file must not be mistaken for this run's output.
        if findings_path.exists():
            findings_path.unlink()
        mcp_cfg = state / "mcp" / f"epic-{n}.json"
        if invoke is None:
            write_mcp_config(
                mcp_cfg,
                storage_state=config.storage_state,
                headless=config.headless,
                caps=config.caps,
            )
        prompt = build_epic_prompt(
            epic,
            base_url=config.base_url,
            spec_rel_path=spec_rel,
            findings_abs_path=str(findings_path),
            seed_summary=config.seed_summary,
        )

        _invoke = invoke or default_invoke(config, str(mcp_cfg))
        with Heartbeat(activity_path, phase=f"qa-epic-{n}", story=f"epic-{n}",
                       repo=config.project_root, pid=os.getpid()):
            result = _invoke(prompt, timeout_sec=config.timeout_sec)
        cum += result.cost_usd

        findings = _read_findings(findings_path)
        if findings is None:
            # No machine-readable output — record the epic as blocked rather than silently green.
            findings = {
                "epic": n,
                "summary": (
                    "Agent produced no findings file "
                    f"({'timed out' if result.timed_out else 'error/parse failure'})."
                ),
                "verdicts": [
                    {"story": s["id"], "ac": c["id"], "observable": True, "status": "blocked",
                     "evidence": "no findings file written", "severity": "high"}
                    for s in epic["stories"] for c in s["criteria"]
                ],
            }

        for ev in findings_to_events(n, findings, cum=cum, iter_index=i):
            emit(ev)

        # Aggregate for the report.
        agg = story_gate(
            [{"status": v.get("status")} for v in findings.get("verdicts", [])]
        )
        epic_results.append(
            {"epic": n, "summary": findings.get("summary", ""), "gate": agg,
             "verdicts": findings.get("verdicts", []), "specFile": findings.get("specFile", spec_rel)}
        )

        write_checkpoint(
            state / "checkpoint.json",
            {"updatedAt": "", "stage": f"epic-{n}", "story": f"epic-{n}", "branch": branch,
             "mergeBase": branch, "cumUsd": round(cum, 4),
             "resume": f"loop-qa --state-dir {state_dir}"},
        )

        if config.cost_ceiling_usd is not None and cum >= config.cost_ceiling_usd:
            stop_reason = f"cost ceiling ${config.cost_ceiling_usd} reached"
            break

    # Aggregate artifacts.
    report = _render_report(config.app, branch, epic_results)
    (state / "report.md").write_text(report, encoding="utf-8")
    (state / "findings.json").write_text(
        json.dumps({"app": config.app, "branch": branch, "epics": epic_results}, indent=2),
        encoding="utf-8",
    )

    all_green = bool(epic_results) and all(er["gate"]["green"] for er in epic_results)
    emit({"event": "stop", "ok": True, "reason": stop_reason, "cum": round(cum, 4),
          "green": all_green})
    return 0


def _read_findings(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None
