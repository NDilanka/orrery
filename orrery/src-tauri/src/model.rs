//! Universal `RunState` and sub-structs (PROTOCOL.md §3), plus `LoopDef` (§7) and the
//! `Delta` channel enum (§6). All wire shapes are `camelCase` JSON.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

// ---------------------------------------------------------------------------
// Enums (string unions on the wire)
// ---------------------------------------------------------------------------

/// `type RunStatus = 'idle'|'running'|'stopping'|'stopped'|'quota-wait'|'handoff'|'error';`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum RunStatus {
    Idle,
    Running,
    Stopping,
    Stopped,
    QuotaWait,
    Handoff,
    Error,
}

impl Default for RunStatus {
    fn default() -> Self {
        RunStatus::Idle
    }
}

/// `type SixPhase = 'discover'|'assemble'|'execute'|'verify'|'persist'|'decide';`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum SixPhase {
    Discover,
    Assemble,
    Execute,
    Verify,
    Persist,
    Decide,
}

/// `type ItemStatus = 'backlog'|'ready'|'in-progress'|'review'|'done'|'blocked'|'failed';`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ItemStatus {
    Backlog,
    Ready,
    InProgress,
    Review,
    Done,
    Blocked,
    Failed,
}

impl Default for ItemStatus {
    fn default() -> Self {
        ItemStatus::Backlog
    }
}

/// `type RestState = 'certified-done'|'stopped-ember'|'quota-frost'|'handoff-beacon'|'failed-dark'|null;`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum RestState {
    CertifiedDone,
    StoppedEmber,
    QuotaFrost,
    HandoffBeacon,
    /// a run at rest with `status: Error` (a BMAD `stop{ok:false}`) — outranks every other rest
    /// state (§4 rule 5): a crashed run needs attention most.
    FailedDark,
}

/// `'haiku'|'sonnet'|'opus'`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ModelTier {
    Haiku,
    Sonnet,
    Opus,
}

/// `stopPending: 'phase'|'story'|'now'|null`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum StopPending {
    Phase,
    Story,
    Now,
}

/// `resetType: 'five_hour'|'weekly'|null`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResetType {
    FiveHour,
    Weekly,
}

/// Group lifecycle: `'backlog'|'in-progress'|'done'`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum GroupStatus {
    Backlog,
    InProgress,
    Done,
}

impl Default for GroupStatus {
    fn default() -> Self {
        GroupStatus::Backlog
    }
}

/// Retro lifecycle: `'optional'|'done'|'pending'|null`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RetroStatus {
    Optional,
    Done,
    Pending,
}

/// QA kind: `'review'|'retro'`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum QaKind {
    Review,
    Retro,
}

/// `answeredBy: 'agent'|'ui'|null`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AnsweredBy {
    Agent,
    Ui,
}

// ---------------------------------------------------------------------------
// RunState and sub-structs (§3)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunState {
    pub loop_id: String,
    pub run: Run,
    /// epics (rings). generic loops may have 0-1 group.
    pub groups: BTreeMap<String, Group>,
    /// stories / iterations-as-one-item
    pub items: BTreeMap<String, WorkItem>,
    pub current_item: Option<String>,
    pub phase: Phase,
    pub cost: Cost,
    pub quota: Quota,
    pub cache: Cache,
    /// run-quality summary (§2 engine-v3 `metrics` event); `null` until seen.
    pub metrics: Option<Metrics>,
    pub questions: Vec<Qa>,
    /// by item key, latest
    pub verdicts: BTreeMap<String, Verdict>,
    /// raw event count
    pub events: u64,
}

impl RunState {
    pub fn new(loop_id: impl Into<String>) -> Self {
        RunState {
            loop_id: loop_id.into(),
            run: Run::default(),
            groups: BTreeMap::new(),
            items: BTreeMap::new(),
            current_item: None,
            phase: Phase::default(),
            cost: Cost::default(),
            quota: Quota::default(),
            cache: Cache::default(),
            metrics: None,
            questions: Vec::new(),
            verdicts: BTreeMap::new(),
            events: 0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Run {
    pub status: RunStatus,
    /// which "not-running" palette, if any
    pub rest_state: Option<RestState>,
    pub pid: Option<i64>,
    /// current work item / goal
    pub target: Option<String>,
    pub branch: Option<String>,
    pub merge_base: String,
    /// RUNNING-MAX of every cum/cumUsd seen (never additive)
    pub cum_usd: f64,
    /// from checkpoint
    pub stage: Option<String>,
    pub stop_pending: Option<StopPending>,
    pub resume_cmd: Option<String>,
    pub started_at: Option<String>,
    /// ts of the last applied log event (independent of `updatedAt`, which is checkpoint-only).
    pub last_event_at: Option<String>,
    pub updated_at: Option<String>,
}

impl Default for Run {
    fn default() -> Self {
        Run {
            status: RunStatus::Idle,
            rest_state: None,
            pid: None,
            target: None,
            branch: None,
            merge_base: "main".to_string(),
            cum_usd: 0.0,
            stage: None,
            stop_pending: None,
            resume_cmd: None,
            started_at: None,
            last_event_at: None,
            updated_at: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Group {
    pub id: String,
    pub status: GroupStatus,
    pub retro_status: Option<RetroStatus>,
    pub ring_index: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkItem {
    pub key: String,
    pub group: Option<String>,
    pub index: i64,
    pub status: ItemStatus,
    pub gate: Option<Gate>,
    pub smoke: Option<Smoke>,
    pub pr: Option<Pr>,
    /// frozen AC contract. Always serialized (as `null` when absent) so the wire shape matches
    /// reduce.ts, which always emits `ghost: null`.
    pub ghost: Option<Ghost>,
    pub strikes: i64,
    pub strike_budget: i64,
    /// verifier sealed it (vs claimed-green)
    pub certified: bool,
    pub cost_attributed: f64,
    /// for sprint-status vs live-event conflict resolution
    pub last_event_ts: f64,
}

impl WorkItem {
    pub fn new(key: impl Into<String>) -> Self {
        WorkItem {
            key: key.into(),
            group: None,
            index: 0,
            status: ItemStatus::Backlog,
            gate: None,
            smoke: None,
            pr: None,
            ghost: None,
            strikes: 0,
            strike_budget: 3,
            certified: false,
            cost_attributed: 0.0,
            last_event_ts: 0.0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Gate {
    pub green: bool,
    pub pass: i64,
    pub fail: i64,
    pub total: i64,
    pub baseline_pass: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stages: Option<Vec<Stage>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Stage {
    pub name: String,
    pub ok: bool,
    pub exit: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Smoke {
    pub iter: i64,
    pub passed: bool,
    pub verdict: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timed_out: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Pr {
    pub url: String,
    pub base: String,
    pub merged: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Ghost {
    pub criteria: Vec<Criterion>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Criterion {
    pub text: String,
    pub met: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Phase {
    pub name: Option<String>,
    pub label: Option<String>,
    pub six_phase: Option<SixPhase>,
    pub model: Option<ModelTier>,
}

impl Default for Phase {
    fn default() -> Self {
        Phase {
            name: None,
            label: None,
            six_phase: None,
            model: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Cost {
    pub cum_usd: f64,
    pub ceiling_usd: Option<f64>,
    /// last threshold crossed
    pub alert_pct: Option<i64>,
    pub series: Vec<CostSample>,
    pub rate_per_min: f64,
}

impl Default for Cost {
    fn default() -> Self {
        Cost {
            cum_usd: 0.0,
            ceiling_usd: None,
            alert_pct: None,
            series: Vec::new(),
            rate_per_min: 0.0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CostSample {
    /// ms since epoch
    pub t: f64,
    pub cum: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Quota {
    pub active: bool,
    pub label: Option<String>,
    pub reset_at: Option<String>,
    pub resume_at: Option<String>,
    pub reset_type: Option<ResetType>,
    pub probe: i64,
    pub wait_sec: i64,
}

impl Default for Quota {
    fn default() -> Self {
        Quota {
            active: false,
            label: None,
            reset_at: None,
            resume_at: None,
            reset_type: None,
            probe: 0,
            wait_sec: 0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Cache {
    pub hit_ratio: f64,
    pub warm: bool,
}

impl Default for Cache {
    fn default() -> Self {
        Cache {
            hit_ratio: 0.0,
            warm: false,
        }
    }
}

/// §2 engine-v3 `metrics` event — a run-quality fold of the event stream
/// (`loop.metrics.compute_metrics`). `None` until a `metrics` event is seen;
/// first-try-green + iters/cost-to-green replace pass@k for loops.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Metrics {
    pub first_try_green: bool,
    pub iters_to_green: Option<i64>,
    pub cost_to_green: Option<f64>,
    pub rollbacks: i64,
    pub regression_rate: f64,
    pub total_iters: i64,
    pub total_cost: f64,
    pub final_green: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Qa {
    pub id: String,
    pub kind: QaKind,
    pub turn: i64,
    pub q: String,
    pub a: Option<String>,
    pub summary: Option<String>,
    pub answered_by: Option<AnsweredBy>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub epic: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Verdict {
    pub pass: bool,
    pub failing_criteria: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub evidence: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_action: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
}

// ---------------------------------------------------------------------------
// LoopDef (§7)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LoopDef {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub theme: Option<String>,
    /// 'generic' | 'external'
    #[serde(default)]
    pub kind: Option<String>,
    pub state_dir: String,
    /// 'generic' | 'bmad' | 'custom'
    pub adapter: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_file: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stop_flag: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub checkpoint: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub start: Option<StartSpec>,
    /// present when kind=generic (the 6 blocks as config); kept opaque here
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub engine: Option<serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartSpec {
    pub program: String,
    #[serde(default)]
    pub args: Vec<String>,
}

// ---------------------------------------------------------------------------
// A6 — live control wire shapes (§6 / §7)
// ---------------------------------------------------------------------------

/// `checkpoint.json` (§7): resume state written by the engine at safe boundaries.
/// `resume` is a shell command string (e.g. `pwsh -File "...bmad-loop.ps1"`).
/// All fields optional/defaulted because external engines may omit some.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Checkpoint {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stage: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub story: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub branch: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub merge_base: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cum_usd: Option<f64>,
    /// resume = a shell command string
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resume: Option<String>,
}

/// Result of `start_loop` / `resume_loop` (§6): `{ pid }`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartResult {
    pub pid: u32,
}

/// Result of `guard_status` (§6): `{ running, pid, stopPending, checkpoint }`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GuardStatus {
    pub running: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    /// raw contents of the STOP file ("phase"|"story"|"now"), if present
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stop_pending: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub checkpoint: Option<Checkpoint>,
}

// ---------------------------------------------------------------------------
// Activity — the liveness heartbeat (`<stateDir>/activity.json`, PROTOCOL §1)
// ---------------------------------------------------------------------------

/// A single liveness beat the engine overwrites every few seconds DURING a long agent step, so a
/// watcher can tell "actively working" from a hung loop between the coarse phase-boundary events
/// in `log.jsonl`. State, not event (a single overwritten file like `checkpoint.json`). All fields
/// optional/defaulted — a malformed `activity.json` deserializes to defaults rather than erroring.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Activity {
    /// ISO-8601 (UTC, `Z`) write time — the UI derives freshness from `now - ts`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ts: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub phase: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub story: Option<String>,
    /// Seconds the current agent step has been running.
    #[serde(default)]
    pub elapsed_sec: f64,
    /// Count of changed files in the repo work tree — a live "actually producing work" signal.
    #[serde(default)]
    pub dirty: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pid: Option<i64>,
}

// ---------------------------------------------------------------------------
// Delta channel enum (§6)
// ---------------------------------------------------------------------------

/// `Delta = { kind: 'snapshot', state } | { kind: 'event', event } | { kind: 'state', state }
///        | { kind: 'activity', activity }`
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum Delta {
    Snapshot { state: RunState },
    Event { event: serde_json::Value },
    State { state: RunState },
    /// liveness heartbeat (`activity.json`); `activity` is `null` when the file is absent/cleared.
    Activity { activity: Option<Activity> },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delta_wire_shape_matches_protocol() {
        let snap = Delta::Snapshot {
            state: RunState::new("x"),
        };
        let v = serde_json::to_value(&snap).unwrap();
        assert_eq!(v["kind"], "snapshot");
        assert!(v.get("state").is_some());

        let ev = Delta::Event {
            event: serde_json::json!({"event":"iter"}),
        };
        let v = serde_json::to_value(&ev).unwrap();
        assert_eq!(v["kind"], "event");
        assert_eq!(v["event"]["event"], "iter");

        let st = Delta::State {
            state: RunState::new("x"),
        };
        assert_eq!(serde_json::to_value(&st).unwrap()["kind"], "state");

        // activity delta: tagged kind + a camelCase Activity body (or null).
        let act = Delta::Activity {
            activity: Some(Activity {
                ts: Some("2026-06-24T17:15:00Z".into()),
                phase: Some("dev-story".into()),
                story: Some("5-2".into()),
                elapsed_sec: 252.0,
                dirty: 3,
                pid: Some(7240),
            }),
        };
        let v = serde_json::to_value(&act).unwrap();
        assert_eq!(v["kind"], "activity");
        assert_eq!(v["activity"]["elapsedSec"], 252.0, "elapsedSec camelCase");
        assert_eq!(v["activity"]["phase"], "dev-story");

        // a null beat (file cleared/absent) round-trips as activity: null
        let none = Delta::Activity { activity: None };
        assert!(serde_json::to_value(&none).unwrap()["activity"].is_null());

        // a malformed activity.json (extra/missing fields) deserializes to defaults, never errors.
        let lax: Activity = serde_json::from_value(serde_json::json!({"phase":"x","bogus":1})).unwrap();
        assert_eq!(lax.phase.as_deref(), Some("x"));
        assert_eq!(lax.dirty, 0);
        assert_eq!(lax.elapsed_sec, 0.0);
    }

    #[test]
    fn runstate_uses_camelcase_keys() {
        let v = serde_json::to_value(&RunState::new("loop1")).unwrap();
        assert!(v.get("loopId").is_some(), "loopId camelCase");
        assert!(v.get("currentItem").is_some(), "currentItem camelCase");
        assert!(v["run"].get("cumUsd").is_some(), "run.cumUsd camelCase");
        assert!(v["run"].get("restState").is_some(), "run.restState camelCase");
        assert!(v["run"].get("stopPending").is_some(), "run.stopPending camelCase");
        assert!(v["cost"].get("ratePerMin").is_some(), "cost.ratePerMin camelCase");
        assert!(v["quota"].get("resetAt").is_some(), "quota.resetAt camelCase");
        assert!(v["cache"].get("hitRatio").is_some(), "cache.hitRatio camelCase");
    }
}
