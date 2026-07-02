"""``run_loop`` — the generic fix-until-green driver, ported from ``loop.ps1`` (~363-806).

This is the keystone that wires every already-built pure module into the live loop the
PowerShell ``loop.ps1`` runs: INIT (baseline gate + hash-lock + frozen contract + concurrency
guard) then the main FOR loop (stop-flag check -> execute -> cost/cache -> gate + integrity ->
Q&A -> optional verify -> decide -> emit iter -> act). The DECISION logic itself lives in the
pure cores (:mod:`loop.decide`, :mod:`loop.gate`, :mod:`loop.hashlock`, …); this module is the
imperative shell that calls them in order and performs the side effects (events, git, sleep).

Nothing here spawns ``claude`` directly: every agent turn goes through the injected
``runner`` (an :class:`~loop.runners.base.AgentRunner`), and every event is written through
:func:`loop.logio.append_event`, so the whole loop is driveable with a mock runner + a callable
gate stage (see ``tests/test_core.py``) with no real model, no real sleep, and no real network.

Deliberate simplifications vs ``loop.ps1`` (each a clean follow-up, none change the core
wiring):

- **No ``-AutoDecide`` / judge sub-agent calls.** The Q&A surface detects a ``QUESTION:``
  marker and consumes a UI ``answer.json`` if one matches, but does NOT call a cheap-model
  decider. The optional VERIFY pass runs the judge through the SAME ``runner`` and parses with
  :func:`loop.verdict.parse_verdict`; ``config.verify.contract`` (or the parsed contract) is the
  frozen criteria.
- **No ``-Fresh`` template reset.** The caller manages ``progress.md`` lifecycle.
- **Concurrency guard is a pid lockfile**, not a process-table scan — simpler and OS-portable.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from loop import gitutil
from loop.cache import get_cache_usage
from loop.checkpoint import get_stop_mode
from loop.config import EngineConfig
from loop.cost import update_cost_alert
from loop.decide import decide, update_consecutive_fail
from loop.events import (
    cache_event,
    cooperative_stop_event,
    cost_alert_event,
    gate_event,
    handoff_event,
    iter_event,
    metrics_event,
    model_event,
    new_checkpoint,
    parse_error_event,
    phase_timeout_event,
    plateau_event,
    review_answer_event,
    review_question_event,
    rollback_event,
    stop_event,
    verdict_event,
)
from loop.feedback import compact_feedback
from loop.gate import run_gate
from loop.hashlock import compare_hash_map, test_hash_map
from loop.heartbeat import Heartbeat
from loop.lockfile import LOCK_NAME, acquire_lock, release_lock
from loop.memory import FileMemoryStore, Lesson, NullMemoryStore
from loop.metrics import compute_metrics
from loop.verify import (
    held_out_green,
    held_out_lock_globs,
    held_out_names,
    mutation_audit,
    visible_feedback_raw,
)
from loop.logio import (
    append_event,
    consume_answer,
    read_answer_inbox,
    read_stop_flag,
    read_text,
    write_checkpoint,
    write_run_output,
    write_text,
)
from loop.quota import survive
from loop.verdict import contract_criteria, parse_verdict, question_marker
from loop.verdict import read_answer_inbox as match_answer_inbox

def _stages_for_gate(config: EngineConfig) -> list[dict]:
    """Render the typed ``GateStage`` list into the dict shape ``run_gate`` expects.

    A callable command (the test hook) is passed straight through; a string command runs via
    the shell inside ``run_gate``.
    """
    out: list[dict] = []
    for s in config.gate.stages:
        out.append(
            {
                "name": s.name,
                "command": s.command,
                "pass_pattern": s.pass_pattern,
                "fail_pattern": s.fail_pattern,
                "held_out": s.held_out,
                "lock_globs": list(s.lock_globs),
            }
        )
    return out


def _lock_glob_set(config: EngineConfig, stages: list[dict]) -> list[str]:
    """The hash-lock glob set: the configured lock globs PLUS any held-out stages' globs.

    With no held-out stage this is just the first configured glob (or ``"*"``) — byte-identical
    to the prior single-glob behavior. Held-out globs are merged in (de-duped, order-stable) so
    the hidden test files are tamper-protected (loop.verify.held_out_lock_globs).
    """
    base = [config.gate.lock_globs[0]] if config.gate.lock_globs else ["*"]
    out: list[str] = list(base)
    for g in held_out_lock_globs(stages):
        if g not in out:
            out.append(g)
    return out


def _hash_map_over(globs: list[str], work: Path) -> dict[str, str]:
    """Union of :func:`test_hash_map` over several lock globs (keyed by relative path)."""
    merged: dict[str, str] = {}
    for g in globs:
        merged.update(test_hash_map(g, work))
    return merged


def _read_first_line(path: Path) -> str:
    """First line of a text file (``""`` when absent/empty) — for the ``BLOCKED:`` probe."""
    txt = read_text(path)
    if not txt:
        return ""
    return txt.splitlines()[0] if txt.splitlines() else ""


def run_loop(
    config: EngineConfig,
    *,
    runner,
    state_dir,
    cwd,
    dry_run: bool = False,
) -> int:
    """Run the generic fix-until-green loop. Returns a process exit code.

    ``0`` = stopped green (or a clean cooperative stop / dry-run); ``1`` = handoff (max-iters,
    tamper, plateau, cost, …); ``2`` = refused (a live concurrency lock exists).

    ``runner`` is an :class:`~loop.runners.base.AgentRunner`; ``state_dir`` holds
    ``log.jsonl`` / ``checkpoint.json`` / ``STOP`` / ``progress.md`` / ``answer.json`` /
    ``lock``; ``cwd`` is the repo the gate + git operate on. ``dry_run`` does INIT + one
    baseline gate, prints a plan, and returns 0 WITHOUT calling the runner.
    """
    state = Path(state_dir)
    work = Path(cwd)
    state.mkdir(parents=True, exist_ok=True)

    log_path = state / "log.jsonl"
    checkpoint_path = state / "checkpoint.json"
    stop_path = state / "STOP"
    progress_path = state / "progress.md"
    answer_path = state / "answer.json"
    lock_path = state / LOCK_NAME

    # Metrics: when enabled, keep an in-memory copy of every event we append to the log so
    # compute_metrics(events) can fold the whole run at stop. OFF -> no list, no metrics event.
    emitted: list[dict] | None = [] if config.metrics.emit else None

    def emit(event: dict) -> None:
        append_event(log_path, event)
        if emitted is not None:
            emitted.append(event)

    # --- concurrency guard --------------------------------------------------
    if not acquire_lock(lock_path):
        print(f"[GUARD] another loop is already running against state dir '{state}'.")
        return 2

    try:
        return _run_loop_inner(
            config,
            runner=runner,
            work=work,
            state=state,
            log_path=log_path,
            checkpoint_path=checkpoint_path,
            stop_path=stop_path,
            progress_path=progress_path,
            answer_path=answer_path,
            emit=emit,
            emitted=emitted,
            dry_run=dry_run,
        )
    finally:
        # Release the lock no matter how we exit (mirrors "remove on exit"). Also runs when a
        # KeyboardInterrupt/other exception unwinds through this try (proc.py has already killed
        # any child tree by then; cli.py turns a KeyboardInterrupt into a clean exit(130)).
        release_lock(lock_path)


def _run_loop_inner(
    config: EngineConfig,
    *,
    runner,
    work: Path,
    state: Path,
    log_path: Path,
    checkpoint_path: Path,
    stop_path: Path,
    progress_path: Path,
    answer_path: Path,
    emit,
    emitted: list[dict] | None,
    dry_run: bool,
) -> int:
    # --- memory store: FileMemoryStore when enabled, else the OFF Null store ----------
    if config.memory.enabled:
        mem_path = config.memory.path or (state / "memory.jsonl")
        memory = FileMemoryStore(mem_path)
    else:
        memory = NullMemoryStore()

    rc = _run_loop_body(
        config,
        runner=runner,
        work=work,
        state=state,
        log_path=log_path,
        checkpoint_path=checkpoint_path,
        stop_path=stop_path,
        progress_path=progress_path,
        answer_path=answer_path,
        emit=emit,
        emitted=emitted,
        dry_run=dry_run,
        memory=memory,
    )

    # --- shutdown side effects (skipped for dry-run, which emits nothing) --------------
    if not dry_run:
        # Metrics: fold every event we appended and emit ONE additive metrics event (the
        # stop event is already in `emitted`, so the green moment is captured). OFF -> no-op.
        if emitted is not None:
            m = compute_metrics(emitted)
            emit(
                metrics_event(
                    first_try_green=m["first_try_green"],
                    iters_to_green=m["iters_to_green"],
                    cost_to_green=m["cost_to_green"],
                    rollbacks=m["rollbacks"],
                    regression_rate=m["regression_rate"],
                    total_iters=m["total_iters"],
                    total_cost=m["total_cost"],
                    final_green=m["final_green"],
                )
            )
        # Memory: one bounded compaction at shutdown (NullMemoryStore -> no-op).
        try:
            memory.prune(max_entries=200)
        except Exception:
            pass

    return rc


def _run_loop_body(
    config: EngineConfig,
    *,
    runner,
    work: Path,
    state: Path,
    log_path: Path,
    checkpoint_path: Path,
    stop_path: Path,
    progress_path: Path,
    answer_path: Path,
    emit,
    emitted: list[dict] | None,
    dry_run: bool,
    memory,
) -> int:
    # --- crash recovery: restore any leftover mutation-audit backup from a hard-killed prior
    # run BEFORE the baseline gate reads the tree (Task 3 — a mutated source file left behind by
    # a SIGKILL/power-loss mid-audit must never be mistaken for the user's real code).
    _recover_mutation_backups(state, work, progress_path)

    # The checkpoint `resume` string for THIS run — computed once, reused at every checkpoint
    # write (Task 5: carries the real task/cwd/state-dir + --loop-json, not a hardcoded default).
    resume_cmd = _resume_command(config, state, work)

    stages = _stages_for_gate(config)
    execute_model = config.model_for("execute")
    judge_model = config.model_for("judge")
    item = Path(config.task).name or "goal"
    run_id = f"{int(time.time())}-{os.getpid()}"

    # --- held-out (hidden) test split: which stages are hidden + their lock globs ----
    hidden_names = held_out_names(stages)
    has_held_out = bool(hidden_names)

    recall_limit = config.memory.recall_limit

    # --- INIT: git, baseline gate, hash-lock baseline, frozen contract ------
    use_git = gitutil.is_repo(work)
    branch = gitutil.current_branch(work) if use_git else None
    merge_base = gitutil.head(work) if use_git else None

    lock_globs = _lock_glob_set(config, stages)
    baseline_map = _hash_map_over(lock_globs, work)

    # Frozen acceptance-criteria contract: explicit config.verify.contract wins; else parse the
    # task file's '## Acceptance Criteria' / '## Definition of done' section.
    if config.verify.contract:
        contract = list(config.verify.contract)
    else:
        task_text = read_text(work / config.task) or read_text(config.task) or ""
        contract = contract_criteria(task_text)

    # Baseline gate (no event under dry_run, mirroring loop.ps1 ~566). The gate's string-command
    # stages run in the loop's working dir so `--cwd` targets the right repo.
    base = run_gate(stages, str(work))
    base_total = base["total"]
    best_pass = base["pass"]
    best_commit = gitutil.head(work) if use_git else None

    if not dry_run:
        emit(model_event("execute", execute_model))
        if config.verify and getattr(config.verify, "enabled", False):
            emit(model_event("judge", judge_model))
        emit(
            gate_event(
                cum=0.0,
                green=base["green"],
                pass_=base["pass"],
                fail=base["fail"],
                total=base["total"],
                stages=base["stages"],
            )
        )

    print(
        f"Baseline: {base['pass']}/{base['total']} pass  green={base['green']}  task={config.task}"
    )

    # --- dry-run: plan summary, no runner -----------------------------------
    if dry_run:
        stage_names = ",".join(s["name"] for s in stages)
        print(
            f"DryRun OK — gate wired. git={use_git}  bestPass={best_pass}  "
            f"stages=[{stage_names}]  lockGlobs={config.gate.lock_globs}  "
            f"tools={len(config.allowed_tools)}  permMode={config.permission_mode}  "
            f"maxTurns={config.max_turns}  execModel={execute_model}  "
            f"contractCriteria={len(contract)}"
        )
        print("No runner calls, no quota spent.")
        return 0

    # Already green at baseline -> a clean green stop (loop.ps1 ~583).
    if base["green"]:
        emit(stop_event("already green at baseline", True, 0, 0.0, best_pass))
        _write_checkpoint(checkpoint_path, "done", branch, merge_base, 0.0, resume_cmd)
        return 0

    # --- loop state ---------------------------------------------------------
    # Per-iter wall-clock cap (config.iter_timeout_min, minutes; 0 = disabled/unbounded).
    iter_timeout_sec = config.iter_timeout_min * 60 if config.iter_timeout_min > 0 else 0
    cum = 0.0
    stale = 0
    plateau = 0
    regress_count = 0
    consec_fail = 0
    consec_recovered = False
    plateau_alerted = False
    green_seen = 0  # green gates seen so far (for mutation_every throttling)
    fired: list[int] = []

    # Memory recall is folded into the STABLE prefix (after the frozen contract, before the
    # volatile steer) so it is cache-friendly. NullMemoryStore -> "" -> byte-identical prompt.
    recall = memory.recall(config.task, limit=recall_limit)
    prompt = _build_prompt(config, contract, stages, recall=recall)

    max_iters = config.stop.max_iters
    for it in range(1, max_iters + 1):
        print(f"\n=== iteration {it}/{max_iters}  (cum ${round(cum, 4)}  best {best_pass}/{base_total}) ===")

        # 1. cooperative stop-if-requested at the SAFE (between-iteration) boundary.
        req = get_stop_mode(read_stop_flag(stop_path), scope="story")
        if req["honor"]:
            if use_git and gitutil.is_dirty(work):
                gitutil.add_all(work)
                gitutil.commit(work, f"loop: cooperative stop ({req['mode']}) at iter {it}")
                branch = gitutil.current_branch(work)
            _write_checkpoint(checkpoint_path, f"iter {it}", branch, merge_base, cum, resume_cmd)
            emit(
                cooperative_stop_event(
                    scope="story",
                    mode=req["mode"],
                    stage=f"iter {it}",
                    story=None,
                    branch=branch or "",
                    cum=cum,
                )
            )
            # consume the flag
            try:
                os.remove(stop_path)
            except OSError:
                pass
            print(f"[STOPPED] graceful stop honored ({req['mode']}) at iter {it}")
            return 0

        # 2 + 3. EXECUTE (with quota-survival retry — the same iter is retried on recovery).
        # A liveness Heartbeat overwrites <stateDir>/activity.json for the duration of each
        # (blocking) execute call, mirroring ResilientRunner (loop.bmad.driver) so the UI has the
        # same freshness signal for a generic loop's execute phase as it does for BMAD phases.
        result = None
        while True:
            with Heartbeat(state / "activity.json", phase="execute", story=f"iter {it}", repo=work):
                result = runner.run(
                    prompt=prompt,
                    model=execute_model,
                    allowed_tools=config.allowed_tools,
                    permission_mode=config.permission_mode,
                    max_turns=config.max_turns,
                    cwd=str(work),
                    timeout_sec=iter_timeout_sec,
                )
            if result.timed_out:
                emit(phase_timeout_event(f"iter {it}", iter_timeout_sec))
                print(f"  [TIMEOUT] iter {it} killed (hung runner)")
                result = None
                break
            if result.quota_limited or (result.is_error and result.quota_limited):
                recovered = survive(
                    runner,
                    label=f"iter {it}",
                    cum=cum,
                    emit=emit,
                    sleep=time.sleep,
                )
                if recovered:
                    continue  # retry the SAME iteration — no iter consumed
                # Could not recover -> terminate. loop.ps1 (660-663) emits NO handoff in the
                # quota path; it just stops. We mirror that for wire parity so the reducer's
                # restState reads 'stopped-ember' (a stop) and not 'handoff-beacon'.
                # TODO(parity): reconsider raising a deliberate beacon here as an improvement.
                emit(stop_event("quota limit — could not resume", False, it, cum, best_pass))
                _write_checkpoint(checkpoint_path, "handoff", branch, merge_base, cum, resume_cmd)
                return 1
            if result.parse_failed:
                # Agent output did NOT parse to JSON ($parsed -eq $null, loop.ps1:662) — even a
                # non-empty garbage result. After the quota check, before the gate: emit
                # parse_error and STOP (green=False).
                emit(parse_error_event(it))
                emit(stop_event("could not parse runner output", False, it, cum, best_pass))
                _write_checkpoint(checkpoint_path, "handoff", branch, merge_base, cum, resume_cmd)
                return 1
            break  # productive (or a non-quota error result we fall through with)

        # Persist this iteration's raw agent output for postmortem — previously thrown away
        # entirely, so a failed/parse-failed run left no artifact. No-op when there's nothing to
        # write (result is None on a timeout, or raw is empty).
        if result is not None:
            write_run_output(state / f"run-{it}.out", result.raw)

        # 4. cost accounting + 50/80/100% alerts, then cache telemetry.
        iter_cost = float(result.cost_usd) if result else 0.0
        cum += iter_cost
        alert = update_cost_alert(cum, config.cost.ceiling_usd, config.cost.alert_pct, fired)
        fired = alert.fired
        for pct in alert.newly:
            emit(cost_alert_event(pct, cum, config.cost.ceiling_usd))

        if result is not None:
            cu = get_cache_usage(result.usage)
            emit(cache_event(cu.hit_ratio, cu.warm))

        # 5. GATE + integrity signals (string stages run in the loop's working dir).
        g = run_gate(stages, str(work))
        emit(
            gate_event(
                cum=cum,
                green=g["green"],
                pass_=g["pass"],
                fail=g["fail"],
                total=g["total"],
                stages=g["stages"],
            )
        )
        hash_now = _hash_map_over(lock_globs, work)
        tamper = compare_hash_map(baseline_map, hash_now)
        tampered = bool(tamper["tampered"])
        count_dropped = g["total"] < base_total
        blocked = _read_first_line(progress_path).startswith("BLOCKED:")
        changed = gitutil.is_dirty(work) if use_git else True

        # 5b. Held-out (hidden) green is ALREADY part of g["green"] (run_gate ANDs every stage's
        # exit). We re-state it explicitly for clarity; the emitted gate event above carries the
        # TRUE combined pass/fail/total/green — only the AGENT-VISIBLE feedback hides the suite.
        combined_green = g["green"] and held_out_green(g, hidden_names)

        # 5c. Volatile feedback steer -> progress.md. When red, write the agent-visible gate
        # feedback: held-out sections stripped FIRST (so the hidden suite never leaks), then
        # compacted to the first failure when feedback.compact is on. Default off + no held-out
        # stage -> the raw gate dump (today's behavior).
        if not combined_green:
            _write_feedback_steer(config, g, hidden_names, has_held_out, progress_path, it)

        # 6. Q&A surface (minimal): QUESTION marker -> review-question; consume a matching answer.
        result_text = result.text if result else ""
        _resolve_question(
            it, result_text, progress_path, answer_path, emit
        )

        # 7. optional VERIFY (anti-false-green) — only when enabled AND gate green AND intact.
        verifier_refuted = False
        if (
            getattr(config.verify, "enabled", False)
            and g["green"]
            and not tampered
            and not count_dropped
        ):
            verifier_refuted = _run_verify(
                runner, config, contract, judge_model, work, use_git, progress_path, it, emit
            )

        # 7b. optional MUTATION AUDIT (advisory) — only when enabled AND the gate is green,
        # throttled by mutation_every. Emits a `mutation` log line carrying the score. The probe
        # ALWAYS restores the file in a finally (bulletproof — never left mutated).
        if config.verify.mutation_audit and g["green"]:
            green_seen += 1
            every = max(1, config.verify.mutation_every)
            if green_seen % every == 0:
                _run_mutation_audit(config, stages, work, it, emit, state)

        # update drift counters before the verdict.
        if not changed:
            stale += 1
        else:
            stale = 0
        if changed and g["pass"] == best_pass:
            plateau += 1
        elif g["pass"] != best_pass:
            plateau = 0
            plateau_alerted = False

        # plateau trend-alert (one-shot per episode).
        if plateau >= config.stop.plateau_limit and not plateau_alerted:
            plateau_alerted = True
            emit(plateau_event(item, plateau))
            print(f"  [PLATEAU] {item}: pass-count flat across {plateau} changed iters")

        # 8. DECIDE.
        dec = decide(
            green=g["green"],
            tampered=tampered,
            count_dropped=count_dropped,
            blocked=blocked,
            pass_=g["pass"],
            best_pass=best_pass,
            changed=changed,
            regress_count=regress_count,
            regress_limit=config.stop.regress_limit,
            plateau=plateau,
            plateau_limit=config.stop.plateau_limit,
            stale=stale,
            stagnation_limit=config.stop.stagnation_limit,
            cum=cum,
            ceiling=config.cost.ceiling_usd,
            iter=it,
            max_iters=max_iters,
            verifier_refuted=verifier_refuted,
        )
        action = dec.action
        green = dec.green
        reason = dec.reason
        if tampered:
            # Surface the named-file tamper reason (loop.ps1 ~739).
            reason = str(tamper["reason"])

        # consecutive-failure -> recover-once -> handoff (overrides only continue/rollback).
        made_progress = g["pass"] > best_pass
        cf = update_consecutive_fail(
            green=g["green"],
            made_progress=made_progress,
            count=consec_fail,
            recovered=consec_recovered,
            limit=config.stop.stagnation_limit,
        )
        consec_fail = cf.count
        consec_recovered = cf.recovered
        if action in ("continue", "rollback"):
            if cf.handoff:
                emit(handoff_event(item, cf.reason, cf.count))
                print(f"  [HANDOFF] {item}: {cf.reason}")
                action, green, reason = "stop", False, cf.reason
            elif cf.recover:
                print(f"  [RECOVER] {cf.reason} — resetting to best-known-good and retrying once")
                if use_git and best_commit:
                    gitutil.reset_hard(work, best_commit)
                _append_progress(
                    progress_path,
                    f"\n## Recover hint (iter {it})\nStuck after {cf.count} no-progress iters. "
                    f"Tree reset to best-known-good ({best_pass}/{base_total}). "
                    f"Re-read {config.task} and try a DIFFERENT minimal fix.",
                )

        # 9. emit the iter event (all PROTOCOL fields).
        emit(
            iter_event(
                iter=it,
                cost=iter_cost,
                cum=cum,
                pass_=g["pass"],
                total=g["total"],
                best=best_pass,
                changed=changed,
                stale=stale,
                plateau=plateau,
                regress=regress_count,
                action=action,
                reason=reason,
            )
        )
        print(
            f"  -> {g['pass']}/{g['total']} pass  iter_cost=${iter_cost}  "
            f"cum=${round(cum, 4)}  [{action}]"
        )

        # 9a. LIVE METRICS — an updated `metrics` event folded over every event so far this run,
        # so a watching UI has a fresh run-quality read every iteration instead of only at stop.
        # Additive to (does not replace) the single authoritative metrics event emitted at
        # shutdown (_run_loop_inner); reducers treat repeated `metrics` events as last-write-wins.
        # OFF by default (config.metrics.emit) -> `emitted` is None -> no-op, byte-identical to
        # today.
        if emitted is not None:
            live_m = compute_metrics(emitted)
            emit(
                metrics_event(
                    first_try_green=live_m["first_try_green"],
                    iters_to_green=live_m["iters_to_green"],
                    cost_to_green=live_m["cost_to_green"],
                    rollbacks=live_m["rollbacks"],
                    regression_rate=live_m["regression_rate"],
                    total_iters=live_m["total_iters"],
                    total_cost=live_m["total_cost"],
                    final_green=live_m["final_green"],
                )
            )

        # 9b. MEMORY — record a green-gated lesson for this productive iteration. NullMemoryStore
        # makes this a no-op; FileMemoryStore green-gates + de-dupes internally so only useful
        # ('green'/'progress') outcomes persist.
        outcome = _iter_outcome(action, green, made_progress)
        memory.record_if_useful(
            Lesson(
                text=_lesson_text(it, g, action, hidden_names),
                kind="episodic",
                task=config.task,
                outcome=outcome,
                run_id=run_id,
                iter=it,
                created_ts=time.time(),
            )
        )

        # 10. ACT.
        if action == "stop":
            if g["pass"] > best_pass:
                best_pass = g["pass"]
            if "repeated regressions" in reason:
                emit(handoff_event(item, reason, regress_count + 1))
                print(f"  [HANDOFF] {item}: {reason}")
            if green and use_git and changed:
                gitutil.add_all(work)
                gitutil.commit(work, f"loop {it}: GREEN {g['pass']}/{g['total']}")
            if not green and use_git and best_commit:
                gitutil.reset_hard(work, best_commit)
            emit(stop_event(reason, green, it, cum, best_pass))
            _write_checkpoint(
                checkpoint_path, "done" if green else "handoff", branch, merge_base, cum, resume_cmd
            )
            return 0 if green else 1

        if action == "rollback":
            if use_git and best_commit:
                gitutil.reset_hard(work, best_commit)
            regress_count += 1
            emit(
                rollback_event(
                    item,
                    to_iter=it,
                    best_pass=best_pass,
                    strike=regress_count,
                    strike_budget=config.stop.regress_limit,
                )
            )
            print(
                f"  rollback -> best {best_pass}/{base_total} "
                f"(strike {regress_count}/{config.stop.regress_limit})"
            )
        elif action == "continue":
            if use_git and changed:
                gitutil.add_all(work)
                gitutil.commit(work, f"loop {it}: {g['pass']}/{g['total']} pass")
            if g["pass"] > best_pass:  # new high-water mark
                best_pass = g["pass"]
                regress_count = 0
                if use_git:
                    best_commit = gitutil.head(work)

    # Fell out of the for loop: max iterations reached without green.
    emit(
        stop_event(
            f"max iterations ({max_iters}) reached without green",
            False,
            max_iters,
            cum,
            best_pass,
        )
    )
    if use_git and best_commit:
        gitutil.reset_hard(work, best_commit)
    _write_checkpoint(checkpoint_path, "handoff", branch, merge_base, cum, resume_cmd)
    return 1


def _build_prompt(
    config: EngineConfig, contract: list[str], stages: list[dict], *, recall: str = ""
) -> str:
    """Assemble the stable execute prompt via :mod:`loop.prompts`.

    ``recall`` (a cross-run lessons block from :mod:`loop.memory`) is inserted into the stable
    prefix; empty by default so the prompt is byte-identical to the pre-memory version.
    """
    from loop.prompts import execute_prompt

    gate_hint = stages[0]["command"] if stages and isinstance(stages[0]["command"], str) else "the gate command"
    return execute_prompt(contract, config.task, gate_hint=gate_hint, recall=recall)


def _resolve_question(it: int, result_text: str, progress_path: Path, answer_path: Path, emit) -> None:
    """Q&A surface: detect a QUESTION marker; consume a matching UI ``answer.json``.

    Looks in the agent result body first, then progress.md (first marker wins). On a marker,
    emit ``review-question``; if a matching ``answer.json`` is present, emit ``review-answer``,
    consume it, and feed the answer back through progress.md.
    """
    progress_text = read_text(progress_path) or ""
    q = question_marker(result_text) or question_marker(progress_text)
    if not q:
        return
    emit(review_question_event(it, q))
    print(f"  [Q&A] question (turn {it}): {q}")

    content = read_answer_inbox(answer_path)
    if content is None:
        print("  [Q&A] no answer.json — proceeding unanswered")
        return
    inbox = match_answer_inbox(content, it)
    if inbox["matched"]:
        consume_answer(answer_path)
        emit(review_answer_event(it, inbox["a"]))
        print(f"  [Q&A] answered from UI inbox: {inbox['a']}")
        _append_progress(
            progress_path,
            f"\n## Answer (turn {it}, from UI)\nQUESTION: {q}\nANSWER: {inbox['a']}\n"
            "Proceed accordingly; clear the QUESTION line.",
        )


def _run_verify(runner, config, contract, judge_model, work, use_git, progress_path, it, emit) -> bool:
    """Optional anti-false-green VERIFY pass; returns True when the verifier REFUTED green.

    A SECOND, independent judge (cheap tier) sees ONLY the diff + the frozen contract and tries
    to refute "done". Parsed via :func:`loop.verdict.parse_verdict`; a fail suppresses the
    green-stop and feeds the failing criteria back through progress.md.
    """
    diff = ""
    if use_git:
        r = gitutil._git(["diff", "HEAD"], work)
        diff = r.stdout or ""
    contract_text = "\n".join(f"- {c}" for c in contract)
    judge_prompt = (
        "You are an independent VERIFIER. REFUTE the claim that the work is done. You see "
        "ONLY a git diff and a FROZEN acceptance-criteria contract.\n\n"
        f"FROZEN ACCEPTANCE CRITERIA:\n{contract_text}\n\nGIT DIFF:\n{diff}\n\n"
        'Respond with ONLY JSON: {"pass": <bool>, "failingCriteria": [..], '
        '"evidence": "..", "nextAction": ".."}'
    )
    res = runner.run(
        prompt=judge_prompt,
        model=judge_model,
        allowed_tools=["Read"],
        permission_mode="plan",
        max_turns=1,
        cwd=str(work),
        output_format="text",
    )
    verdict = parse_verdict(res.text, config.task, judge_model)
    emit(verdict)  # parse_verdict already returns the verdict_event shape
    if not verdict["pass"]:
        fc = "\n".join(f"- {c}" for c in verdict["failingCriteria"])
        _append_progress(
            progress_path,
            f"\n## Verifier refuted (iter {it})\n{fc}\nNext: {verdict['nextAction']}",
        )
        print(f"  [VERIFY] REFUTED green — failing: {'; '.join(verdict['failingCriteria'])}")
        return True
    print(f"  [VERIFY] certified green (model={judge_model})")
    return False


def _write_feedback_steer(
    config: EngineConfig,
    gate_result: dict,
    hidden_names: list[str],
    has_held_out: bool,
    progress_path: Path,
    it: int,
) -> None:
    """Write the AGENT-VISIBLE gate feedback into progress.md (the volatile steer).

    Held-out sections are stripped FIRST via :func:`loop.verify.visible_feedback_raw` so the
    hidden suite never leaks; then, when ``config.feedback.compact`` is on, the visible feedback
    is reduced to the FIRST failing stage's first failure via
    :func:`loop.feedback.compact_feedback`. Default off + no held-out stage -> the raw gate dump
    (today's behavior, just routed through progress.md).
    """
    # Strip the hidden sections first so neither the raw nor the compact path can leak them.
    visible_raw = visible_feedback_raw(gate_result, hidden_names) if has_held_out else (
        gate_result.get("raw") or ""
    )

    if config.feedback.compact:
        # compact_feedback slices per-stage off ``raw``; feed it the already-stripped raw so
        # the first VISIBLE failing stage is what surfaces (never a hidden one).
        visible_result = dict(gate_result)
        visible_result["raw"] = visible_raw
        body = compact_feedback(visible_result)
    else:
        body = visible_raw

    if not body or not body.strip():
        return
    _append_progress(
        progress_path,
        f"\n## Gate feedback (iter {it})\n{body.strip()}\n",
    )


def _mutation_backup_path(state: Path, work: Path, target: Path) -> Path:
    """Where the durable, crash-safe backup of ``target`` lives (mirrors its position under
    ``work`` so :func:`_recover_mutation_backups` can restore it to the exact right path)."""
    try:
        rel = target.relative_to(work)
    except ValueError:
        rel = Path(target.name)
    return state / "mutation-backup" / rel


def _recover_mutation_backups(state: Path, work: Path, progress_path: Path) -> None:
    """INIT-time recovery for a mutation-audit backup left behind by a hard crash (Task 3).

    :func:`_run_mutation_audit` writes a durable ON-DISK backup before mutating a file and
    deletes it after a clean restore. If a backup still exists here, the previous run was killed
    (SIGKILL / power loss / OOM) before its in-process ``finally`` could restore the file from
    it — the target may still hold mutated (broken) text. Restore every leftover backup to its
    original path, delete it, and note the recovery so it's visible before anything else runs
    (in particular before the baseline gate reads the tree).
    """
    backup_root = state / "mutation-backup"
    if not backup_root.is_dir():
        return
    restored: list[str] = []
    for backup_file in backup_root.rglob("*"):
        if not backup_file.is_file():
            continue
        rel = backup_file.relative_to(backup_root)
        target = work / rel
        try:
            content = backup_file.read_text(encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            backup_file.unlink()
            restored.append(rel.as_posix())
        except OSError:
            continue  # best-effort — leave it for the next INIT to retry
    if restored:
        msg = (
            "restored " + ", ".join(restored) + " from a leftover mutation-audit backup "
            "(a previous run crashed mid-mutation)."
        )
        print(f"[RECOVER] {msg}")
        _append_progress(progress_path, f"\n## Mutation-audit recovery\n{msg}\n")
    # Best-effort cleanup of now-empty backup directories.
    try:
        dirs = sorted((p for p in backup_root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
        for d in dirs:
            if not any(d.iterdir()):
                d.rmdir()
        if backup_root.is_dir() and not any(backup_root.iterdir()):
            backup_root.rmdir()
    except OSError:
        pass


def _run_mutation_audit(
    config: EngineConfig,
    stages: list[dict],
    work: Path,
    it: int,
    emit,
    state: Path,
) -> float | None:
    """Run the advisory mutation-strength audit over the changed implementation file.

    The ``source`` is the changed implementation file's text. ``run_tests(mutated_source)``
    writes the mutated text to the file, runs the gate, returns gate-green, and ALWAYS restores
    the original file in a ``finally`` from a DURABLE ON-DISK backup (Task 3) — not just an
    in-memory string — so a hard kill mid-mutant leaves a recoverable backup rather than a
    corrupted user source file (see :func:`_recover_mutation_backups`, run at the next INIT).
    Returns the mutation score (``killed / mutants``), or ``None`` when no target file could be
    identified. Advisory only: it never gates the run.
    """
    target = _impl_target_file(config, work)
    if target is None:
        return None
    try:
        original = target.read_text(encoding="utf-8")
    except OSError:
        return None

    backup_path = _mutation_backup_path(state, work, target)
    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(original, encoding="utf-8")
    except OSError:
        # Can't guarantee a crash-safe restore for this file -> skip the (advisory-only) audit
        # this iteration rather than risk mutating it with no durable way back.
        return None

    def run_tests(mutated_source: str) -> bool:
        """Write the mutant, run the gate, ALWAYS restore from the durable on-disk backup."""
        try:
            target.write_text(mutated_source, encoding="utf-8")
            g = run_gate(stages, str(work))
            return bool(g["green"])
        finally:
            # Restore from the ON-DISK backup (not the in-memory `original` string) — the file on
            # disk, not the process's memory, is what survives a hard kill.
            target.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")

    try:
        audit = mutation_audit(original, run_tests)
    finally:
        # Every run_tests call already restored `target` from the backup; the audit is now done
        # (or raised) with the file back to `original`. Delete the backup — a SURVIVING backup is
        # exactly the signal _recover_mutation_backups looks for on the next INIT.
        try:
            backup_path.unlink()
        except OSError:
            pass
    score = audit["score"]
    # Additive `mutation` log event (NOT in the golden corpus; reducer ignores unknown events).
    emit(
        {
            "event": "mutation",
            "iter": it,
            "score": score,
            "mutants": audit["mutants"],
            "killed": audit["killed"],
            "survived": audit["survived"],
        }
    )
    print(
        f"  [MUTATION] iter {it}: score={score:.2f} "
        f"({audit['killed']}/{audit['mutants']} killed, {audit['survived']} survived)"
    )
    return score


def _impl_target_file(config: EngineConfig, work: Path) -> Path | None:
    """Best-effort locate the changed implementation file for the mutation audit.

    Prefers the most-recently-modified tracked source file under ``work`` that is NOT the task
    spec, a test/lock file, or under a dotted/state dir. Returns ``None`` when nothing fits.
    """
    candidates: list[tuple[float, Path]] = []
    for p in work.rglob("*"):
        if not p.is_file():
            continue
        name = p.name
        rel = p.relative_to(work).as_posix()
        if rel == config.task or name == "TASK.md" or name == "progress.md":
            continue
        if any(part.startswith(".") for part in p.relative_to(work).parts):
            continue
        if "test" in name.lower() or name.endswith(".md") or name.endswith(".json"):
            continue
        try:
            candidates.append((p.stat().st_mtime, p))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def _iter_outcome(action: str, green: bool, made_progress: bool) -> str:
    """Map a decision into a memory ``outcome`` label ('green'|'progress'|'regress'|'handoff')."""
    if green and action == "stop":
        return "green"
    if action == "rollback":
        return "regress"
    if action == "stop":  # a non-green stop is a handoff
        return "handoff"
    return "progress" if made_progress else "handoff"


def _lesson_text(it: int, gate_result: dict, action: str, hidden_names: list[str]) -> str:
    """A short outcome / first-failure summary for a recorded :class:`Lesson`.

    On a failing gate, lead with the first VISIBLE failure (held-out sections stripped) so the
    lesson never embeds the hidden suite's text; on green, a terse pass summary.
    """
    if gate_result.get("green"):
        return f"iter {it}: reached green at {gate_result.get('pass')}/{gate_result.get('total')}."
    visible = dict(gate_result)
    visible["raw"] = visible_feedback_raw(gate_result, hidden_names) if hidden_names else (
        gate_result.get("raw") or ""
    )
    first = compact_feedback(visible)
    head = first.splitlines()[0] if first else "gate red"
    return f"iter {it} ({action}): {head}"


def _append_progress(progress_path: Path, text: str) -> None:
    """Append ``text`` to progress.md (creating it if absent)."""
    existing = read_text(progress_path) or ""
    write_text(progress_path, existing + text)


def _resume_command(config: EngineConfig, state: Path, work: Path) -> str:
    """Reconstruct the ``loop`` command that resumes THIS run (checkpoint ``resume``, PROTOCOL
    §7). Mirrors :func:`loop.bmad.driver._resume_command`: carries the REAL task/cwd/state-dir
    (not a hardcoded placeholder) and, when the run was launched with one, re-points at
    ``--loop-json`` so tuning that has no other CLI surface (e.g. custom gate stages, verify
    contract) survives a resume/Reignite instead of silently reverting to defaults.
    """

    def q(value: object) -> str:
        s = str(value)
        return f'"{s}"' if (" " in s or "\t" in s) else s

    parts = ["loop", "--task", q(config.task), "--cwd", q(work), "--state-dir", q(state)]
    if config.loop_json:
        parts += ["--loop-json", q(config.loop_json)]
    return " ".join(parts)


def _write_checkpoint(
    checkpoint_path: Path, stage: str, branch, merge_base, cum: float, resume: str
) -> None:
    """Write a PROTOCOL §7 checkpoint.json via the pure builder + the logio writer."""
    cp = new_checkpoint(
        stage=stage,
        story=None,
        branch=branch or "",
        merge_base=merge_base or "",
        cum_usd=cum,
        resume=resume,
    )
    write_checkpoint(checkpoint_path, cp)


# Re-export so a verdict_event symbol exists for callers/tests if they want the shape.
__all__ = ["run_loop", "verdict_event"]
