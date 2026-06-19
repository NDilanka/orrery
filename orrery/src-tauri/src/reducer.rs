//! Pure reducer (PROTOCOL.md §4).
//!
//! `apply(state, event, ts) -> ()` mutates state in place. Re-applying the full log twice
//! yields an identical `RunState` (idempotent): items keyed by `key`, groups by `id`,
//! QA by `kind+turn+(epic||"")`, `cumUsd` is a running-max (never additive), cost series
//! appends, `restState` derived last, `certified` flips on `verdict{pass:true}`.
//!
//! The reducer parses each line into `serde_json::Value`, reads `obj["event"]`, and matches.
//! Field order is non-deterministic, so we key on names not positions. Unknown events bump the
//! event counter and are otherwise ignored.

use crate::model::*;
use serde_json::Value;

const RATE_WINDOW: usize = 8;

/// Holds the reduced state plus a little reducer-private bookkeeping that is not on the wire.
pub struct Reducer {
    pub state: RunState,
}

impl Reducer {
    pub fn new(loop_id: impl Into<String>, adapter: impl Into<String>) -> Self {
        // adapter is accepted for symmetry / future per-adapter defaults; the reducer is
        // event-driven and handles both supersets, so it is not needed for state init today.
        let _adapter = adapter.into();
        let state = RunState::new(loop_id);
        Reducer { state }
    }

    /// Apply one already-parsed JSON event with a caller-supplied timestamp (ms since epoch).
    pub fn apply(&mut self, obj: &Value, ts: f64) {
        self.state.events += 1;

        let event = match obj.get("event").and_then(Value::as_str) {
            Some(e) => e,
            None => return, // not an event object; ignore but count
        };

        // running-max cumUsd (reset on a new `start`, see below) + cost series sample
        // is handled per-event where a cum is present.
        match event {
            // ---- Core generic engine ------------------------------------------------
            "iter" => self.on_iter(obj, ts),
            "stop" => self.on_stop(obj, ts),
            "parse_error" => { /* counted only */ }

            // ---- Core engine v2 additions -------------------------------------------
            "gate" => self.on_gate(obj, ts),
            "verdict" => self.on_verdict(obj, ts),
            "model" => self.on_model(obj),
            "cost-alert" => self.on_cost_alert(obj, ts),
            "cache" => self.on_cache(obj),
            "plateau" => self.on_plateau(obj),
            "rollback" => self.on_rollback(obj),
            "handoff" => self.on_handoff(obj),
            "phase-timeout" => self.on_phase_timeout(obj),

            // ---- Quota --------------------------------------------------------------
            "quota-hit" => self.on_quota_hit(obj, ts),
            "quota-wait" => self.on_quota_wait(obj, ts),
            "quota-resume" => self.on_quota_resume(obj),

            // ---- BMAD adapter superset ---------------------------------------------
            "start" => self.on_start(obj),
            "story-start" => self.on_story_start(obj, ts),
            "dev-gate" => self.on_dev_gate(obj, ts),
            "review-question" => self.on_review_question(obj),
            "review-answer" => self.on_review_answer(obj),
            "review-complete" => self.on_review_complete(obj),
            "retro-start" => self.on_retro_start(obj),
            "retro-question" => self.on_retro_question(obj),
            "retro-answer" => self.on_retro_answer(obj),
            "retro-complete" => self.on_retro_complete(obj),
            "smoke-server" => { /* informational; ignored */ }
            "smoke-iter" => self.on_smoke_iter(obj, ts),
            "pr-created" => self.on_pr_created(obj, ts),
            "pr-merged" => self.on_pr_merged(obj, ts),
            "cooperative-stop" => self.on_cooperative_stop(obj, ts),

            // unknown — counted, ignored
            _ => {}
        }

        // restState is derived last, every event (cheap).
        self.derive_rest_state();
    }

    // -----------------------------------------------------------------------
    // cum / cost helpers
    // -----------------------------------------------------------------------

    /// Read a cum value from either `cum` or `cumUsd`.
    fn read_cum(obj: &Value) -> Option<f64> {
        obj.get("cum")
            .and_then(Value::as_f64)
            .or_else(|| obj.get("cumUsd").and_then(Value::as_f64))
    }

    /// running-max cumUsd + append a cost sample. Mirrors run.cumUsd into cost.cumUsd.
    ///
    /// The series is keyed by sample timestamp `t` so a true re-apply of the same log (same
    /// per-index `t`) is idempotent — re-applying upserts the sample rather than duplicating it.
    fn bump_cum(&mut self, obj: &Value, ts: f64) {
        if let Some(c) = Self::read_cum(obj) {
            if c > self.state.run.cum_usd {
                self.state.run.cum_usd = c;
            }
            self.state.cost.cum_usd = self.state.run.cum_usd;
            match self.state.cost.series.iter_mut().find(|s| s.t == ts) {
                Some(existing) => existing.cum = c,
                None => self.state.cost.series.push(CostSample { t: ts, cum: c }),
            }
            self.recompute_rate();
        }
    }

    /// ratePerMin from the last ~N samples (delta cum / delta minutes).
    fn recompute_rate(&mut self) {
        let s = &self.state.cost.series;
        let n = s.len();
        if n < 2 {
            self.state.cost.rate_per_min = 0.0;
            return;
        }
        let start = n.saturating_sub(RATE_WINDOW);
        let first = &s[start];
        let last = &s[n - 1];
        let dt_min = (last.t - first.t) / 60_000.0;
        let dcum = last.cum - first.cum;
        self.state.cost.rate_per_min = if dt_min > 0.0 { dcum / dt_min } else { 0.0 };
    }

    // -----------------------------------------------------------------------
    // item / group helpers
    // -----------------------------------------------------------------------

    fn item_mut(&mut self, key: &str) -> &mut WorkItem {
        self.state
            .items
            .entry(key.to_string())
            .or_insert_with(|| WorkItem::new(key))
    }

    fn parse_item_status(s: &str) -> Option<ItemStatus> {
        Some(match s {
            "backlog" => ItemStatus::Backlog,
            "ready" | "ready-for-dev" => ItemStatus::Ready,
            "in-progress" => ItemStatus::InProgress,
            "review" => ItemStatus::Review,
            "done" => ItemStatus::Done,
            "blocked" => ItemStatus::Blocked,
            "failed" => ItemStatus::Failed,
            _ => return None,
        })
    }

    /// Derive an epic id from a story key like "3-4-..." → "3".
    fn epic_of(story: &str) -> Option<String> {
        story.split('-').next().and_then(|h| {
            if !h.is_empty() && h.chars().all(|c| c.is_ascii_digit()) {
                Some(h.to_string())
            } else {
                None
            }
        })
    }

    /// Ensure a group (epic ring) exists; ringIndex = epic number when numeric.
    fn ensure_group(&mut self, epic: &str) {
        if self.state.groups.contains_key(epic) {
            return;
        }
        let ring = epic.parse::<i64>().unwrap_or(0);
        self.state.groups.insert(
            epic.to_string(),
            Group {
                id: epic.to_string(),
                status: GroupStatus::InProgress,
                retro_status: None,
                ring_index: ring,
            },
        );
    }

    // -----------------------------------------------------------------------
    // Core generic engine
    // -----------------------------------------------------------------------

    fn on_iter(&mut self, obj: &Value, ts: f64) {
        self.bump_cum(obj, ts);
        self.state.run.status = RunStatus::Running;
        self.state.phase.six_phase = Some(SixPhase::Execute);

        // Generic loops fold all iterations into a single synthetic item "iter".
        let pass = obj.get("pass").and_then(Value::as_i64).unwrap_or(0);
        let total = obj.get("total").and_then(Value::as_i64).unwrap_or(0);
        let green = pass > 0 && total > 0 && pass >= total;
        let best = obj.get("best").and_then(Value::as_i64).unwrap_or(0);
        let regress = obj.get("regress").and_then(Value::as_i64).unwrap_or(0);

        let cum = self.state.run.cum_usd;
        let item = self.item_mut("iter");
        item.last_event_ts = ts;
        item.index = 0;
        item.status = if green {
            ItemStatus::Done
        } else {
            ItemStatus::InProgress
        };
        item.strikes = regress;
        item.cost_attributed = cum;
        item.gate = Some(Gate {
            green,
            pass,
            fail: obj.get("fail").and_then(Value::as_i64).unwrap_or(total - pass).max(0),
            total,
            baseline_pass: best,
            stages: None,
        });
        self.state.current_item = Some("iter".to_string());
    }

    fn on_stop(&mut self, obj: &Value, ts: f64) {
        self.bump_cum(obj, ts);
        // BMAD `stop` carries {ok}; generic `stop` carries {green}.
        let ok = obj.get("ok").and_then(Value::as_bool);
        let green = obj.get("green").and_then(Value::as_bool).unwrap_or(false);
        self.state.run.status = RunStatus::Stopped;
        self.state.phase.six_phase = Some(SixPhase::Decide);

        if green {
            // green stop → the synthetic generic item is done
            if let Some(item) = self.state.items.get_mut("iter") {
                item.status = ItemStatus::Done;
                if let Some(g) = item.gate.as_mut() {
                    g.green = true;
                }
            }
        }
        let _ = ok;
    }

    fn on_gate(&mut self, obj: &Value, ts: f64) {
        self.bump_cum(obj, ts);
        self.state.phase.six_phase = Some(SixPhase::Verify);
        let key = obj
            .get("story")
            .and_then(Value::as_str)
            .map(|s| s.to_string())
            .unwrap_or_else(|| "iter".to_string());
        let gate = Self::build_gate(obj);
        let cum = self.state.run.cum_usd;
        let item = self.item_mut(&key);
        item.last_event_ts = ts;
        item.gate = Some(gate);
        item.cost_attributed = cum;
        self.state.current_item = Some(key);
    }

    fn build_gate(obj: &Value) -> Gate {
        let stages = obj.get("stages").and_then(Value::as_array).map(|arr| {
            arr.iter()
                .map(|s| Stage {
                    name: s.get("name").and_then(Value::as_str).unwrap_or("").to_string(),
                    ok: s.get("ok").and_then(Value::as_bool).unwrap_or(false),
                    exit: s.get("exit").and_then(Value::as_i64).unwrap_or(0),
                })
                .collect()
        });
        Gate {
            green: obj.get("green").and_then(Value::as_bool).unwrap_or(false),
            pass: obj.get("pass").and_then(Value::as_i64).unwrap_or(0),
            fail: obj.get("fail").and_then(Value::as_i64).unwrap_or(0),
            total: obj.get("total").and_then(Value::as_i64).unwrap_or(0),
            baseline_pass: obj.get("baselinePass").and_then(Value::as_i64).unwrap_or(0),
            stages,
        }
    }

    fn on_verdict(&mut self, obj: &Value, ts: f64) {
        let key = match obj.get("item").and_then(Value::as_str) {
            Some(k) => k.to_string(),
            None => return,
        };
        let pass = obj.get("pass").and_then(Value::as_bool).unwrap_or(false);
        let verdict = Verdict {
            pass,
            failing_criteria: obj
                .get("failingCriteria")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                .unwrap_or_default(),
            evidence: obj.get("evidence").and_then(Value::as_str).map(String::from),
            next_action: obj.get("nextAction").and_then(Value::as_str).map(String::from),
            model: obj.get("model").and_then(Value::as_str).map(String::from),
        };
        self.state.verdicts.insert(key.clone(), verdict);
        // certified flips true on verdict{pass:true}
        let item = self.item_mut(&key);
        item.last_event_ts = ts;
        if pass {
            item.certified = true;
        }
    }

    fn on_model(&mut self, obj: &Value) {
        if let Some(p) = obj.get("phase").and_then(Value::as_str) {
            self.state.phase.name = Some(p.to_string());
            self.state.phase.six_phase = Self::six_phase_of(p);
        }
        if let Some(m) = obj.get("model").and_then(Value::as_str) {
            self.state.phase.model = match m {
                "haiku" => Some(ModelTier::Haiku),
                "sonnet" => Some(ModelTier::Sonnet),
                "opus" => Some(ModelTier::Opus),
                _ => None,
            };
        }
    }

    fn six_phase_of(phase: &str) -> Option<SixPhase> {
        let p = phase.to_ascii_lowercase();
        Some(if p.contains("create-story") {
            SixPhase::Discover
        } else if p.contains("assemble") {
            SixPhase::Assemble
        } else if p.contains("dev-story") || p.contains("execute") {
            SixPhase::Execute
        } else if p.contains("gate") || p.contains("review") || p.contains("smoke") || p.contains("verify") {
            SixPhase::Verify
        } else if p.contains("pr") || p.contains("persist") {
            SixPhase::Persist
        } else if p.contains("stop") || p.contains("next") || p.contains("decide") {
            SixPhase::Decide
        } else {
            return None;
        })
    }

    fn on_cost_alert(&mut self, obj: &Value, ts: f64) {
        self.bump_cum(obj, ts);
        if let Some(pct) = obj.get("pct").and_then(Value::as_i64) {
            self.state.cost.alert_pct = Some(pct);
        }
        if let Some(ceil) = obj.get("ceiling").and_then(Value::as_f64) {
            self.state.cost.ceiling_usd = Some(ceil);
        }
    }

    fn on_cache(&mut self, obj: &Value) {
        if let Some(r) = obj.get("hitRatio").and_then(Value::as_f64) {
            self.state.cache.hit_ratio = r;
        }
        if let Some(w) = obj.get("warm").and_then(Value::as_bool) {
            self.state.cache.warm = w;
        }
    }

    fn on_plateau(&mut self, obj: &Value) {
        if let Some(item) = obj.get("item").and_then(Value::as_str) {
            let k = obj.get("k").and_then(Value::as_i64).unwrap_or(0);
            let it = self.item_mut(item);
            it.strikes = k;
        }
    }

    fn on_rollback(&mut self, obj: &Value) {
        if let Some(item) = obj.get("item").and_then(Value::as_str) {
            let strike = obj.get("strike").and_then(Value::as_i64).unwrap_or(0);
            let budget = obj.get("strikeBudget").and_then(Value::as_i64).unwrap_or(0);
            let it = self.item_mut(item);
            it.strikes = strike;
            it.strike_budget = budget;
        }
    }

    fn on_handoff(&mut self, _obj: &Value) {
        self.state.run.status = RunStatus::Handoff;
    }

    fn on_phase_timeout(&mut self, obj: &Value) {
        if let Some(label) = obj.get("label").and_then(Value::as_str) {
            self.state.phase.label = Some(label.to_string());
        }
    }

    // -----------------------------------------------------------------------
    // Quota
    // -----------------------------------------------------------------------

    fn on_quota_hit(&mut self, obj: &Value, ts: f64) {
        self.bump_cum(obj, ts);
        self.state.quota.active = true;
        self.state.run.status = RunStatus::QuotaWait;
        self.state.quota.label = obj.get("label").and_then(Value::as_str).map(String::from);
        self.state.quota.reset_at = obj.get("resetAt").and_then(Value::as_str).map(String::from);
    }

    fn on_quota_wait(&mut self, obj: &Value, ts: f64) {
        self.bump_cum(obj, ts);
        self.state.quota.active = true;
        self.state.run.status = RunStatus::QuotaWait;
        self.state.quota.label = obj.get("label").and_then(Value::as_str).map(String::from);
        self.state.quota.resume_at = obj.get("resumeAt").and_then(Value::as_str).map(String::from);
        self.state.quota.wait_sec = obj.get("waitSec").and_then(Value::as_i64).unwrap_or(0);
        self.state.quota.probe = obj.get("probe").and_then(Value::as_i64).unwrap_or(0);
        self.state.quota.reset_type = match obj.get("resetType").and_then(Value::as_str) {
            Some("five_hour") => Some(ResetType::FiveHour),
            Some("weekly") => Some(ResetType::Weekly),
            _ => self.state.quota.reset_type,
        };
    }

    fn on_quota_resume(&mut self, obj: &Value) {
        self.state.quota.active = false;
        self.state.quota.probe = obj.get("probe").and_then(Value::as_i64).unwrap_or(self.state.quota.probe);
        // resuming work
        self.state.run.status = RunStatus::Running;
    }

    // -----------------------------------------------------------------------
    // BMAD adapter
    // -----------------------------------------------------------------------

    fn on_start(&mut self, obj: &Value) {
        // A new run begins. Per-run running-max → reset the high-water mark so the final
        // cumUsd reflects the *current* run (matches checkpoint.cumUsd & test expectation).
        self.state.run.cum_usd = 0.0;
        self.state.cost.cum_usd = 0.0;
        self.state.run.status = RunStatus::Running;
        self.state.quota.active = false;
        self.state.run.rest_state = None;

        if let Some(target) = obj.get("target").and_then(Value::as_str) {
            self.state.run.target = Some(target.to_string());
            self.state.current_item = Some(target.to_string());
            // ensure the item + its epic group exist
            if let Some(epic) = Self::epic_of(target) {
                self.ensure_group(&epic);
                let it = self.item_mut(target);
                if it.group.is_none() {
                    it.group = Some(epic);
                }
            } else {
                let _ = self.item_mut(target);
            }
        }
        if let Some(branch) = obj.get("branch").and_then(Value::as_str) {
            self.state.run.branch = Some(branch.to_string());
        }
        if let Some(bp) = obj.get("baselinePass").and_then(Value::as_i64) {
            let target = self.state.run.target.clone();
            if let Some(t) = target {
                let it = self.item_mut(&t);
                if let Some(g) = it.gate.as_mut() {
                    g.baseline_pass = bp;
                }
            }
        }
    }

    fn on_story_start(&mut self, obj: &Value, ts: f64) {
        let story = match obj.get("story").and_then(Value::as_str) {
            Some(s) => s.to_string(),
            None => return,
        };
        let status = obj
            .get("status")
            .and_then(Value::as_str)
            .and_then(Self::parse_item_status)
            .unwrap_or(ItemStatus::InProgress);
        let index = obj.get("index").and_then(Value::as_i64).unwrap_or(0);
        // explicit epic field, else derive from key
        let epic = obj
            .get("epic")
            .and_then(Value::as_str)
            .map(String::from)
            .or_else(|| Self::epic_of(&story));
        if let Some(e) = &epic {
            self.ensure_group(e);
        }
        let it = self.item_mut(&story);
        it.last_event_ts = ts;
        it.index = index;
        it.group = epic;
        // story-start moves it into flight; don't downgrade a finished item
        if it.status != ItemStatus::Done {
            it.status = status;
        }
        self.state.current_item = Some(story.clone());
        self.state.run.target = Some(story);
    }

    fn on_dev_gate(&mut self, obj: &Value, ts: f64) {
        self.bump_cum(obj, ts);
        self.state.phase.six_phase = Some(SixPhase::Verify);
        let story = match obj.get("story").and_then(Value::as_str) {
            Some(s) => s.to_string(),
            None => return,
        };
        if let Some(e) = Self::epic_of(&story) {
            self.ensure_group(&e);
        }
        let gate = Self::build_gate(obj);
        let status = obj.get("status").and_then(Value::as_str).and_then(Self::parse_item_status);
        let cum = self.state.run.cum_usd;
        let epic = Self::epic_of(&story);
        let it = self.item_mut(&story);
        it.last_event_ts = ts;
        it.gate = Some(gate);
        it.cost_attributed = cum;
        if it.group.is_none() {
            it.group = epic;
        }
        if let Some(s) = status {
            // dev-gate green only sets *claimed* green; status from event drives lifecycle
            if it.status != ItemStatus::Done {
                it.status = s;
            }
        }
        self.state.current_item = Some(story);
    }

    fn on_review_question(&mut self, obj: &Value) {
        let turn = obj.get("turn").and_then(Value::as_i64).unwrap_or(0);
        let q = obj.get("q").and_then(Value::as_str).unwrap_or("").to_string();
        self.upsert_qa(QaKind::Review, turn, None, |qa| {
            qa.q = q.clone();
        });
    }

    fn on_review_answer(&mut self, obj: &Value) {
        let turn = obj.get("turn").and_then(Value::as_i64).unwrap_or(0);
        let a = obj.get("a").and_then(Value::as_str).map(String::from);
        self.upsert_qa(QaKind::Review, turn, None, |qa| {
            qa.a = a.clone();
            qa.answered_by = Some(AnsweredBy::Agent);
        });
    }

    fn on_review_complete(&mut self, obj: &Value) {
        let turn = obj.get("turn").and_then(Value::as_i64).unwrap_or(0);
        let summary = obj.get("summary").and_then(Value::as_str).map(String::from);
        self.upsert_qa(QaKind::Review, turn, None, |qa| {
            qa.summary = summary.clone();
        });
    }

    fn on_retro_start(&mut self, obj: &Value) {
        if let Some(epic) = obj.get("epic").and_then(Value::as_str) {
            self.ensure_group(epic);
            if let Some(g) = self.state.groups.get_mut(epic) {
                g.retro_status = Some(RetroStatus::Pending);
            }
        }
    }

    fn on_retro_question(&mut self, obj: &Value) {
        let turn = obj.get("turn").and_then(Value::as_i64).unwrap_or(0);
        let epic = obj.get("epic").and_then(Value::as_str).map(String::from);
        let q = obj.get("q").and_then(Value::as_str).unwrap_or("").to_string();
        self.upsert_qa(QaKind::Retro, turn, epic, |qa| {
            qa.q = q.clone();
        });
    }

    fn on_retro_answer(&mut self, obj: &Value) {
        let turn = obj.get("turn").and_then(Value::as_i64).unwrap_or(0);
        let epic = obj.get("epic").and_then(Value::as_str).map(String::from);
        let a = obj.get("a").and_then(Value::as_str).map(String::from);
        self.upsert_qa(QaKind::Retro, turn, epic, |qa| {
            qa.a = a.clone();
            qa.answered_by = Some(AnsweredBy::Agent);
        });
    }

    fn on_retro_complete(&mut self, obj: &Value) {
        let turn = obj.get("turn").and_then(Value::as_i64).unwrap_or(0);
        let epic = obj.get("epic").and_then(Value::as_str).map(String::from);
        let summary = obj.get("summary").and_then(Value::as_str).map(String::from);
        if let Some(e) = &epic {
            if let Some(g) = self.state.groups.get_mut(e) {
                g.retro_status = Some(RetroStatus::Done);
            }
        }
        self.upsert_qa(QaKind::Retro, turn, epic, |qa| {
            qa.summary = summary.clone();
        });
    }

    /// Upsert a QA keyed by kind+turn+(epic||""). Latest write wins per field.
    fn upsert_qa<F: FnOnce(&mut Qa)>(&mut self, kind: QaKind, turn: i64, epic: Option<String>, f: F) {
        let kind_str = match kind {
            QaKind::Review => "review",
            QaKind::Retro => "retro",
        };
        let id = format!("{}-{}-{}", kind_str, turn, epic.as_deref().unwrap_or(""));
        let pos = self.state.questions.iter().position(|q| q.id == id);
        match pos {
            Some(i) => f(&mut self.state.questions[i]),
            None => {
                let mut qa = Qa {
                    id,
                    kind,
                    turn,
                    q: String::new(),
                    a: None,
                    summary: None,
                    answered_by: None,
                    epic,
                };
                f(&mut qa);
                self.state.questions.push(qa);
            }
        }
    }

    fn on_smoke_iter(&mut self, obj: &Value, ts: f64) {
        self.state.phase.six_phase = Some(SixPhase::Verify);
        let smoke = Smoke {
            iter: obj.get("iter").and_then(Value::as_i64).unwrap_or(0),
            passed: obj.get("passed").and_then(Value::as_bool).unwrap_or(false),
            verdict: obj.get("verdict").and_then(Value::as_str).unwrap_or("").to_string(),
            timed_out: obj.get("timedOut").and_then(Value::as_bool),
        };
        // attach to current item if known
        if let Some(cur) = self.state.current_item.clone() {
            let it = self.item_mut(&cur);
            it.last_event_ts = ts;
            it.smoke = Some(smoke);
        }
    }

    fn on_pr_created(&mut self, obj: &Value, ts: f64) {
        self.state.phase.six_phase = Some(SixPhase::Persist);
        let story = obj
            .get("story")
            .and_then(Value::as_str)
            .map(String::from)
            .or_else(|| self.state.current_item.clone());
        let url = obj.get("url").and_then(Value::as_str).unwrap_or("").to_string();
        let base = obj.get("base").and_then(Value::as_str).unwrap_or("").to_string();
        if let Some(key) = story {
            let it = self.item_mut(&key);
            it.last_event_ts = ts;
            it.pr = Some(Pr {
                url,
                base,
                merged: it.pr.as_ref().map(|p| p.merged).unwrap_or(false),
            });
        }
    }

    fn on_pr_merged(&mut self, obj: &Value, ts: f64) {
        self.state.phase.six_phase = Some(SixPhase::Persist);
        let story = match obj
            .get("story")
            .and_then(Value::as_str)
            .map(String::from)
            .or_else(|| self.state.current_item.clone())
        {
            Some(s) => s,
            None => return,
        };
        let pr_url = obj.get("pr").and_then(Value::as_str).unwrap_or("").to_string();
        let base = obj.get("base").and_then(Value::as_str).unwrap_or("").to_string();
        let it = self.item_mut(&story);
        it.last_event_ts = ts;
        match it.pr.as_mut() {
            Some(p) => {
                p.merged = true;
                if !pr_url.is_empty() {
                    p.url = pr_url;
                }
                if !base.is_empty() {
                    p.base = base;
                }
            }
            None => {
                it.pr = Some(Pr {
                    url: pr_url,
                    base,
                    merged: true,
                });
            }
        }
        // pr-merged → item status done
        it.status = ItemStatus::Done;
    }

    fn on_cooperative_stop(&mut self, obj: &Value, ts: f64) {
        self.bump_cum(obj, ts);
        self.state.run.status = RunStatus::Stopped;
        self.state.phase.six_phase = Some(SixPhase::Decide);
        if let Some(stage) = obj.get("stage").and_then(Value::as_str) {
            self.state.run.stage = Some(stage.to_string());
        }
        if let Some(branch) = obj.get("branch").and_then(Value::as_str) {
            self.state.run.branch = Some(branch.to_string());
        }
        // story may be null when between stories
        match obj.get("story") {
            Some(Value::String(s)) => self.state.run.target = Some(s.clone()),
            _ => {}
        }
    }

    // -----------------------------------------------------------------------
    // restState derivation (§4 rule 5)
    // -----------------------------------------------------------------------

    fn derive_rest_state(&mut self) {
        // all items done & merged → certified-done
        let all_done_merged = !self.state.items.is_empty()
            && self.state.items.values().all(|it| {
                it.status == ItemStatus::Done && it.pr.as_ref().map(|p| p.merged).unwrap_or(false)
            });

        let rest = if all_done_merged {
            Some(RestState::CertifiedDone)
        } else if self.state.run.status == RunStatus::Handoff {
            Some(RestState::HandoffBeacon)
        } else if self.state.quota.active {
            Some(RestState::QuotaFrost)
        } else if self.state.run.status == RunStatus::Stopped {
            Some(RestState::StoppedEmber)
        } else {
            None
        };
        self.state.run.rest_state = rest;
    }

    // -----------------------------------------------------------------------
    // Overlays applied by control.rs (checkpoint, sprint-status)
    // -----------------------------------------------------------------------

    /// Apply checkpoint.json fields. cumUsd folds into the running-max.
    pub fn apply_checkpoint(&mut self, cp: &Value) {
        if let Some(v) = cp.get("updatedAt").and_then(Value::as_str) {
            self.state.run.updated_at = Some(v.to_string());
        }
        if let Some(v) = cp.get("stage").and_then(Value::as_str) {
            self.state.run.stage = Some(v.to_string());
        }
        match cp.get("story") {
            Some(Value::String(s)) => self.state.run.target = Some(s.clone()),
            _ => {}
        }
        if let Some(v) = cp.get("branch").and_then(Value::as_str) {
            self.state.run.branch = Some(v.to_string());
        }
        if let Some(v) = cp.get("mergeBase").and_then(Value::as_str) {
            self.state.run.merge_base = v.to_string();
        }
        if let Some(v) = cp.get("cumUsd").and_then(Value::as_f64) {
            if v > self.state.run.cum_usd {
                self.state.run.cum_usd = v;
                self.state.cost.cum_usd = v;
            }
        }
        if let Some(v) = cp.get("resume").and_then(Value::as_str) {
            self.state.run.resume_cmd = Some(v.to_string());
        }
        self.derive_rest_state();
    }

    /// Apply sprint-status.yaml statuses. Authoritative for items NOT in flight: a live event
    /// with a newer `lastEventTs` than the (yaml has none, so ts 0) keeps the live status.
    /// We only set/insert yaml status when the item has no live event (lastEventTs == 0) OR the
    /// item does not yet exist.
    pub fn apply_sprint(&mut self, item_statuses: &[(String, ItemStatus, Option<String>)], group_statuses: &[(String, GroupStatus, Option<RetroStatus>)]) {
        for (key, status, epic) in item_statuses {
            let exists = self.state.items.contains_key(key);
            let live = self
                .state
                .items
                .get(key)
                .map(|it| it.last_event_ts > 0.0)
                .unwrap_or(false);
            if !exists {
                let mut it = WorkItem::new(key);
                it.status = *status;
                it.group = epic.clone();
                self.state.items.insert(key.clone(), it);
            } else if !live {
                // yaml authoritative for not-in-flight items
                if let Some(it) = self.state.items.get_mut(key) {
                    it.status = *status;
                    if it.group.is_none() {
                        it.group = epic.clone();
                    }
                }
            }
        }
        for (id, status, retro) in group_statuses {
            let entry = self.state.groups.entry(id.clone()).or_insert_with(|| Group {
                id: id.clone(),
                status: GroupStatus::Backlog,
                retro_status: None,
                ring_index: id.parse::<i64>().unwrap_or(0),
            });
            // Only let yaml set group status if no live retro/done was recorded; live events
            // set retro_status, but group lifecycle (backlog/in-progress/done) comes from yaml.
            entry.status = *status;
            if entry.retro_status.is_none() {
                entry.retro_status = *retro;
            }
        }
        self.derive_rest_state();
    }
}

/// Reduce a whole batch of already-parsed events into a fresh state.
/// `ts_of` stamps each event; tests use line-index×1000.
pub fn reduce_events(loop_id: &str, adapter: &str, events: &[Value]) -> RunState {
    let mut r = Reducer::new(loop_id, adapter);
    for (i, ev) in events.iter().enumerate() {
        r.apply(ev, (i as f64) * 1000.0);
    }
    r.state
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

    fn read_lines(path: &str) -> Vec<Value> {
        let text = std::fs::read_to_string(path).expect("read fixture");
        text.lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str::<Value>(l).expect("parse line"))
            .collect()
    }

    const BMAD: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../fixtures/bmad-log.jsonl");
    const GENERIC: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../fixtures/generic-log.jsonl");

    #[test]
    fn bmad_reduces_to_expected_state() {
        let events = read_lines(BMAD);
        let state = reduce_events("bmad", "bmad", &events);

        // running-max cumUsd (per-run, resets on `start`) ≈ 26.75
        assert!(
            (state.run.cum_usd - 26.7472).abs() < 0.01,
            "cumUsd was {}",
            state.run.cum_usd
        );
        assert!((state.cost.cum_usd - 26.7472).abs() < 0.01);

        // at least one item done
        let done = state
            .items
            .values()
            .filter(|i| i.status == ItemStatus::Done)
            .count();
        assert!(done >= 1, "expected >=1 done item, got {}", done);

        // currentItem set
        assert!(state.current_item.is_some(), "currentItem should be set");

        // groups non-empty
        assert!(!state.groups.is_empty(), "groups should be non-empty");

        // questions captured
        assert!(!state.questions.is_empty(), "questions should be captured");
    }

    #[test]
    fn generic_reduces_to_green_stop() {
        let events = read_lines(GENERIC);
        let state = reduce_events("roman", "generic", &events);

        assert_eq!(state.run.status, RunStatus::Stopped);
        let item = state.items.get("iter").expect("synthetic iter item");
        assert_eq!(item.status, ItemStatus::Done, "should be done at green stop");
        let gate = item.gate.as_ref().expect("gate");
        assert!(gate.green, "gate should be green");
        assert_eq!(gate.pass, gate.total, "pass==total at the end");
        assert_eq!(gate.pass, 9);
    }

    #[test]
    fn idempotent_double_apply() {
        let events = read_lines(BMAD);

        let once = reduce_events("bmad", "bmad", &events);

        // feed the entire log TWICE, re-stamping the second pass with the same per-index ts so
        // any equality break would come from the reducer, not from monotonic timestamps.
        let mut r = Reducer::new("bmad", "bmad");
        for (i, ev) in events.iter().enumerate() {
            r.apply(ev, (i as f64) * 1000.0);
        }
        for (i, ev) in events.iter().enumerate() {
            r.apply(ev, (i as f64) * 1000.0);
        }
        let twice = r.state;

        // events count differs (raw count), so compare everything else via serialized value
        // minus the `events` field.
        let mut a = serde_json::to_value(&once).unwrap();
        let mut b = serde_json::to_value(&twice).unwrap();
        a.as_object_mut().unwrap().remove("events");
        b.as_object_mut().unwrap().remove("events");
        assert_eq!(a, b, "double-apply must yield identical state (minus raw event count)");
    }

    #[test]
    fn cum_usd_is_running_max_not_additive() {
        // Two events whose cum goes up then down: max wins, never sum.
        let events = vec![
            serde_json::json!({"event":"iter","cum":1.0,"pass":1,"total":3}),
            serde_json::json!({"event":"iter","cum":3.0,"pass":2,"total":3}),
            serde_json::json!({"event":"iter","cum":2.0,"pass":3,"total":3}),
        ];
        let state = reduce_events("x", "generic", &events);
        assert_eq!(state.run.cum_usd, 3.0);
    }

    #[test]
    fn start_resets_running_max() {
        let events = vec![
            serde_json::json!({"event":"start","target":"1-1-foo","branch":"b","baselinePass":0}),
            serde_json::json!({"event":"dev-gate","story":"1-1-foo","cum":50.0,"green":true,"pass":1,"fail":0,"total":1,"baselinePass":0,"status":"done"}),
            serde_json::json!({"event":"start","target":"1-2-bar","branch":"b","baselinePass":0}),
            serde_json::json!({"event":"dev-gate","story":"1-2-bar","cum":5.0,"green":true,"pass":1,"fail":0,"total":1,"baselinePass":0,"status":"done"}),
        ];
        let state = reduce_events("x", "bmad", &events);
        // second run reset to 0 then max is 5.0
        assert_eq!(state.run.cum_usd, 5.0);
    }
}
