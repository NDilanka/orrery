//! Cross-language reducer parity. The committed `tests/golden/*.json` snapshots are
//! the shared CONTRACT for the reduced `RunState`. This asserts the Rust reducer
//! still produces them; the TS suite (`src/lib/reduce.golden.test.ts`) asserts the
//! SAME goldens. If either reducer drifts (from the other or the contract), a test
//! here or there fails. After an INTENTIONAL reducer change, refresh the goldens with
//! `cargo test --test golden_parity -- --ignored regenerate_goldens` and review the diff.
use orrery_lib::reducer::Reducer;
use serde_json::Value;
use std::path::Path;

fn read_lines(path: &Path) -> Vec<Value> {
    let text = std::fs::read_to_string(path).expect("read fixture");
    text.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str::<Value>(l).expect("parse line"))
        .collect()
}

fn reduce_fixture(loop_id: &str, adapter: &str, fixture: &str, checkpoint: Option<&str>) -> Value {
    let base = Path::new(env!("CARGO_MANIFEST_DIR"));
    let fx = base.join("../static/fixtures");
    let events = read_lines(&fx.join(fixture));
    let mut r = Reducer::new(loop_id, adapter);
    for (i, ev) in events.iter().enumerate() {
        let t = ev.get("_t").and_then(Value::as_f64).unwrap_or((i as f64) * 1000.0);
        r.apply(ev, t);
    }
    if let Some(cp) = checkpoint {
        let txt = std::fs::read_to_string(fx.join(cp)).expect("read checkpoint");
        let cpv: Value = serde_json::from_str(&txt).expect("parse checkpoint");
        r.apply_checkpoint(&cpv);
    }
    serde_json::to_value(&r.state).expect("serialize state")
}

fn assert_golden(loop_id: &str, adapter: &str, fixture: &str, checkpoint: Option<&str>, golden: &str) {
    let got = reduce_fixture(loop_id, adapter, fixture, checkpoint);
    let gpath = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden").join(golden);
    let want: Value =
        serde_json::from_str(&std::fs::read_to_string(&gpath).expect("read golden")).expect("parse golden");
    assert_eq!(got, want, "Rust reducer drifted from committed golden {golden}");
}

#[test]
fn parity_demo_bmad() {
    assert_golden("demo", "bmad", "demo-events.jsonl", None, "demo.bmad.json");
}
#[test]
fn parity_bmad() {
    assert_golden("bmad", "bmad", "bmad-log.jsonl", Some("checkpoint.json"), "bmad.bmad.json");
}
#[test]
fn parity_roman_generic() {
    assert_golden("roman", "generic", "roman-log.jsonl", None, "roman.generic.json");
}
#[test]
fn parity_calc_generic() {
    assert_golden("calc", "generic", "calc-log.jsonl", None, "calc.generic.json");
}
#[test]
fn parity_multirun_generic() {
    // generic log with TWO runs (no `start` boundary); cumUsd must scope to the
    // current run (2.0), not the whole-file high-water (5.0).
    assert_golden("multirun", "generic", "multirun-log.jsonl", None, "multirun.generic.json");
}
#[test]
fn parity_series_collision_generic() {
    // two events share `_t` (same ms); both samples must survive in cost.series.
    assert_golden("collision", "generic", "series-collision-log.jsonl", None, "series-collision.generic.json");
}
#[test]
fn parity_metrics_generic() {
    // engine-v3 `metrics` event: state.metrics must be populated (and null for the
    // other fixtures); proves TS and Rust agree on the new run-quality field.
    assert_golden("metrics", "generic", "metrics-log.jsonl", None, "metrics.generic.json");
}
#[test]
fn parity_engine_polish_bmad() {
    // engine-v3 visibility events: `verify` (pass + refute), `test-integrity` (ok+modified +
    // deleted/not-ok), `plan-check` (ok + blocked), and the BMAD FLAVOR of `metrics` (pipeline
    // counters, discriminated from the generic flavor by `storiesCompleted`). Proves Rust and TS
    // agree on all four new events + both metrics flavors, and that the new RunState maps/summary
    // populate (the other fixtures omit them, keeping older goldens byte-identical).
    assert_golden("engine-polish", "bmad", "engine-polish-log.jsonl", None, "engine-polish.bmad.json");
}
#[test]
fn parity_guardrails_generic() {
    // generic-adapter coverage for four documented events that occur ZERO times across the other
    // fixtures: a story-less `gate` (folds into the synthetic "iter" item), `plateau` (sets the
    // iter item's strikes), `parse_error` (counted only), and a TERMINAL `handoff` (→ status
    // 'handoff' → restState 'handoff-beacon'). Also exercises `start` (cumUsd reset) + a generic
    // `stop{green:false}`. Without this case a reducer edit to any of the four would pass parity
    // while diverging in production.
    assert_golden("guardrails", "generic", "guardrails-log.jsonl", None, "guardrails.generic.json");
}
#[test]
fn parity_failed_dark_bmad() {
    // a BMAD stop{ok:false} that ENDS the log (here: retro halted after the epic's only story
    // was already merged) must produce status 'error' + restState 'failed-dark' — and must
    // outrank 'certified-done' even though all items are done & merged. Also exercises
    // `token-usage`: three events feed cache.hitRatio/warm, last write wins.
    assert_golden("failed-dark", "bmad", "failed-dark-log.jsonl", None, "failed-dark.bmad.json");
}

/// Guarded regenerator — does NOT run by default. After an intentional reducer change:
/// `cargo test --test golden_parity -- --ignored regenerate_goldens`, then review the diff.
#[test]
#[ignore]
fn regenerate_goldens() {
    let out = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden");
    std::fs::create_dir_all(&out).unwrap();
    let cases: [(&str, &str, &str, Option<&str>, &str); 10] = [
        ("demo", "bmad", "demo-events.jsonl", None, "demo.bmad.json"),
        ("bmad", "bmad", "bmad-log.jsonl", Some("checkpoint.json"), "bmad.bmad.json"),
        ("roman", "generic", "roman-log.jsonl", None, "roman.generic.json"),
        ("calc", "generic", "calc-log.jsonl", None, "calc.generic.json"),
        ("multirun", "generic", "multirun-log.jsonl", None, "multirun.generic.json"),
        ("collision", "generic", "series-collision-log.jsonl", None, "series-collision.generic.json"),
        ("metrics", "generic", "metrics-log.jsonl", None, "metrics.generic.json"),
        ("engine-polish", "bmad", "engine-polish-log.jsonl", None, "engine-polish.bmad.json"),
        ("failed-dark", "bmad", "failed-dark-log.jsonl", None, "failed-dark.bmad.json"),
        ("guardrails", "generic", "guardrails-log.jsonl", None, "guardrails.generic.json"),
    ];
    for (lid, ad, fx, cp, g) in cases {
        let v = reduce_fixture(lid, ad, fx, cp);
        std::fs::write(out.join(g), serde_json::to_string_pretty(&v).unwrap()).unwrap();
    }
}
