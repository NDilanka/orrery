# Token Efficiency + Quality-First Loop — Findings & Plan

> Why the BMAD loop burned a whole 5-hour Max-5x window on a single story, how Claude's
> subscription limits / pricing / caching actually work, the better ways to invoke Claude Code
> inside the loop (subscription-only — **no API**), and the design for an *efficient AND
> quality-output-first* loop. Findings were produced by fan-out research (primary Anthropic docs
> + adversarial fact-check) and an independent codebase audit.
>
> **Hard constraints that shape every recommendation here:**
> 1. **No Anthropic API.** Subscription-only, via the official `claude` CLI (Max 5x). The loop is
>    **token-constrained**, not dollar-constrained.
> 2. **Preserve fresh-context-per-iteration** (`loop-engineering.md §d`) — it deliberately trades
>    cold-cache cost for zero context rot, and Anthropic's own long-running-agent guidance agrees.
> 3. **Parity discipline** — the engine is a faithful port with a 375-test golden corpus; prefer
>    **additive, default-off** changes.

---

## 0. TL;DR

- **Root cause:** every BMAD phase ran on the **inherited (Opus) model** — including the one-shot
  Q&A *deciders* (which were *designed* for Haiku but had their tier silently overridden) — across
  **~12–40 cold-start `claude -p` processes per story**, each reloading the whole Claude Code
  harness (CLAUDE.md + system prompt + MCP tool schemas + skills) before doing any work. Opus draws
  the **scarce weekly-Opus budget** fastest, so one story could exhaust a window. The loop had **no
  token/cache telemetry**, so the burn was invisible.
- **Shipped this pass (tested, parity-safe):**
  1. **Cost-aware per-phase model tiers** — `decider=haiku`, `create/review/smoke/retro=sonnet`,
     `dev=inherit` (the one phase to spend Opus on). Fixes the all-Opus sink *and* the decider bug
     in one change.
  2. **Per-phase `token-usage` telemetry** — emits the input/output/**cache-read** token counts
     (the real Max-plan meter) per phase + model. The data was already in claude's JSON response
     and being thrown away; capturing it costs **zero** extra tokens.
  3. **Recall-biased code-review** + a quality-oriented dev-story prompt line (see §6).
- **Recommended next (roadmap in §7):** per-phase `--effort` (the verified CLI flag), an adversarial
  **verify-before-merge** Haiku call (the single biggest *quality* lever, ~1 cheap call/story), a
  cheap **plan-gate** before the expensive dev phase, and gate **fail-fast**.

---

## 1. How the Max subscription limits actually work

*Sources: Anthropic support + TechCrunch (Anthropic's own figures), adversarially cross-checked.
Where Anthropic no longer publishes a number, it's flagged as community-estimated.*

- **Two stacked limits.** A **rolling 5-hour session window** (starts on your *first prompt*, resets
  5h later) **plus weekly caps** (introduced Aug 28 2025, reset at a fixed per-account time — matches
  your **Friday ~07:30 Asia/Colombo**). [VERIFIED]
- **Current weekly structure: "all models" + "Sonnet-only."** The original *Opus-specific* weekly cap
  was **replaced** by a Sonnet-only one; Opus now draws against the **all-models** weekly cap (which
  is why bug reports still say "Opus weekly limit hit"). At launch the Max-5x weekly budget was
  ~**140–280 h Sonnet + 15–35 h Opus**; Anthropic has since *stopped publishing* the hour figures, so
  treat them as order-of-magnitude. **Verify your live numbers in Settings → Usage.** [VERIFIED
  structure; figures VERIFIED-historical / UNCERTAIN-current]
- **Metering is token-based and model-weighted.** The "messages" UI number is a simplification —
  every prompt, tool definition, file, and line of history draws tokens. **Opus "uses meaningfully
  more of your quota"** than Sonnet (Anthropic's words). Magnitude: Opus is ~**1.7×** Sonnet on list
  price and emits more output tokens, so call it **~2× the budget draw** of Sonnet for comparable
  work — and the **Opus slice of the weekly budget is the binding constraint long before the overall
  cap.** [Opus-heavier VERIFIED; exact multiplier UNCERTAIN — don't harden "2×"]
- **★ Cache reads barely count against the budget.** Anthropic: *"cache hits are not deducted against
  your rate limit."* A **warm** cached prefix is ~free against the window; a **cold** one pays full
  freight. This is the lever that makes the fresh-context design survivable — *if* the cache is warm.
  [VERIFIED]
- **Claude Code shares the same account meter** as Claude.ai chat, and **agentic** work costs
  **10–50× a single chat message** (tool calls + re-reading files), with multi-agent fan-out ~linear.
  Headless `claude -p` **still draws on the subscription** — the proposed 2026-06-15 "headless → separate
  credit pool" change was **paused before taking effect** (monitor it; Anthropic promised notice
  before retrying). [VERIFIED; "paused" VERIFIED via two independent sources]

**Why this loop is near-worst-case:** per story it runs create-story → dev-story (*unbounded* turns)
→ code-review (≤8 `--resume` turns) → browser-smoke (≤3 MCP iters) → (epic) retro (≤10 turns), each
turn replaying the full transcript, **every phase on Opus**, with a **separate fresh Opus decider per
review/retro question**. That is exactly the "one story per window" shape.

---

## 2. Pricing, caching, and the pause/resume penalty

*Sources: live Anthropic prompt-caching doc + `claude-api` skill, adversarially verified.*

- **List pricing (for intuition; you're not billed per-token on the sub):** Opus 4.8 **$5 / $25**,
  Sonnet 4.6 **$3 / $15**, Haiku 4.5 **$1 / $5** per MTok (in/out).
- **Cache mechanics:** server-side, **prefix-match**, workspace-scoped, **not** tied to a process or
  session id. A brand-new `claude -p` process **hits the cache** *iff* the prefix is byte-identical
  **and** it arrives within the TTL. Cache **read = 0.1×** input; **write = 1.25×** (5-min TTL) /
  **2×** (1-h TTL). The **default TTL dropped to 5 minutes** (Mar 2026). [VERIFIED]
- **The pause penalty.** `quota.survive()` sleeps **hours** to the reset, then re-runs the phase.
  Hours ≫ 1 h ≫ 5 min, so **every resumed phase is unconditionally cold** — it re-pays full-rate
  input on the entire stable prefix (CLAUDE.md + system + tool defs + skills) *and* the write premium.
  Worse, a quota hit *mid-phase* re-runs the whole unbounded phase from scratch, re-spending its
  tokens. [VERIFIED mechanism]
- **Between phases that run close together (<5 min), the fresh processes *do* share cache** — but the
  multi-minute git/PR/gate work between BMAD phases usually blows the 5-min window, so cross-phase
  hit-ratio is likely **~0** today. (Now measurable — see §5.)
- **The #1 caching diagnostic:** confirm `cacheRead > 0` across calls. If it's zero, a **silent
  invalidator** (a timestamp, an unsorted-JSON tool list, a per-request id in the prefix) is killing
  caching and no TTL tuning matters.

---

## 3. The token-sink audit (why it burned)

Independent audit of `engine/loop`, ranked by leverage. (HIGH-severity sinks 1–3 are **fixed**; the
rest are documented as roadmap.)

| # | Sink | Where | Status |
|---|---|---|---|
| 1 | **Every phase inherits Opus** — `DEFAULT_MODELS` all `""` → `--model` omitted → user's default (Opus). The generic loop sensibly tiers `discover=haiku/execute=sonnet/judge=haiku/hard=opus`; the BMAD port threw that away. | `bmad/driver.py` DEFAULT_MODELS | **FIXED (§5)** |
| 2 | **Decider tier bug** — `decider.py` defaults to `haiku`, but the driver passed `model_for("decider")==""`, overriding it back to Opus. Up to ~18 one-shot Q&A cold-starts/story on Opus. | `bmad/driver.py` | **FIXED (§5)** |
| 3 | **No token/cache observability in BMAD** — `ClaudeRunner` parses `usage`, `cache.py` computes hit-ratio, but BMAD logged only USD `cum`. You couldn't *see* the burn. | `bmad/phases.py`, `driver.py` | **FIXED (§5)** |
| 4 | **Quota mid-phase re-runs the whole unbounded turn** from scratch (double-charge). | `driver.py` ResilientRunner | roadmap §7 |
| 5 | **Cold start every phase** — `--resume` only threads *within* the review/retro Q&A loops, never across phases; 5-min cache cold between phases. | `driver.py`, `phases.py` | roadmap §7 |
| 6 | **create-story retries up to 3 full unbounded runs** to recover from a greeting. | `phases.py` create_story | partly fixed (now Sonnet) §5 |
| 7 | **Gate re-runs the full `bun` build at every phase boundary** (≥4×/story). | `driver.py` | roadmap §7 (fail-fast) |
| 8 | **Quota probe is itself a cold-start `claude -p ok`** per wait cycle. | `quota.py` | roadmap §7 |

**Per-story cold-start count:** ~12–20 routine, **50+** worst case (story + retro + quota probes).
Each re-pays the full system-prompt/CLAUDE.md/MCP/skill load — the dominant fixed cost.

---

## 4. Better ways to invoke Claude Code inside the loop (subscription-only)

*Sources: Claude Code docs, the Agent SDK repo/docs, ToS pages, and the `t3code` project.*

**What's NOT an option / what's sanctioned:**
- **Raw Messages API** is API-billed per token — it *defeats* the point of a Max subscription. Off the
  table. [matches your constraint]
- **The official `claude` CLI is the sanctioned automation path.** `claude -p` headless under a Max
  sub is explicitly fine. **`t3code`** (Theo / Ping Labs, `github.com/pingdotgg/t3code`) — a real
  desktop harness — **spawns the local `claude` binary via ChildProcess and never touches OAuth
  tokens**; that's exactly this loop's architecture, and it's the ToS-safe pattern. (Harnesses that
  routed subscription OAuth through custom auth — OpenClaw, Conductor, NanoClaw — were banned.)
- **Claude Agent SDK** (`claude-agent-sdk` Python / `@anthropic-ai/claude-agent-sdk` TS) *also* spawns
  the bundled CLI as a subprocess; its `ClaudeSDKClient` keeps **one warm session across many
  `query()` calls** (a natural fit for the review/retro Q&A loops — N warm turns instead of N×
  `claude -p --resume` cold starts). **Caveat:** SDK-with-subscription-OAuth is a **ToS gray area**;
  the unambiguously-safe path stays "shell to the official `claude` binary." Treat `ClaudeSDKClient`
  as a *documented option to evaluate*, not a drop-in.

**The real, sanctioned levers (all CLI flags — verified, do not invent):**
- **`--model <sonnet|opus|haiku|fable|id>`** — per-phase tier. **(shipped — §5)**
- **`--effort <low|medium|high|xhigh|max>`** — per-phase reasoning depth. Real, `-p`-compatible,
  non-persistent. Effort economics: at *medium*, Opus matched Sonnet's best SWE-bench at **76% fewer
  output tokens**; higher effort is "less likely to declare victory prematurely." **(roadmap §7)**
- **Trim the cold-start footprint** — don't load the chrome-devtools MCP except in browser-smoke
  (already scoped via `SMOKE_TOOLS`); keep CLAUDE.md lean; scope `--allowedTools` tightly. (`--bare`
  skips CLAUDE.md/MCP/skills but **assumes API-key auth**, so it's *not* usable with subscription OAuth
  — manual trimming is the subscription-safe equivalent.)
- **`--append-system-prompt-file <fixed path>`** — inject shared quality rules as a **stable,
  cacheable** system block reused across phases.
- **Keep prompts cache-friendly** — stable prefix first, volatile steer in `progress.md` (the agent
  reads it itself), `--effort`/`--model` are *flags* (changing them doesn't alter prompt bytes, so
  escalating effort on a retry doesn't break the prefix cache).
- **Anthropic's own long-running-agent guidance = this loop's design:** fresh sessions per task,
  bridged by **durable artifacts** (progress file + git + a feature/AC list), *not* one ever-growing
  session. Validates fresh-context-per-iteration.

---

## 5. What shipped this pass

All changes are additive, parity-safe (full 396-test suite green, lint clean), and subscription/CLI-only.

### 5.1 Cost-aware per-phase model tiers — `bmad/driver.py: DEFAULT_MODELS`
```
create=sonnet  dev=""(inherit→Opus)  review=sonnet  smoke=sonnet  retro=sonnet  decider=haiku
```
- Everything except **dev-story** routes to a cheaper tier that's plenty for the task — the
  **external gate (codegen+lint+test) remains the real arbiter of correctness regardless of review
  model.** This single change fixes sinks #1 and #2.
- **dev-story** inherits your Claude Code default. It is the **single heaviest phase** — you
  measured an **Opus dev-story at ~44% of a 5-hour window**. For quota-tight runs **pin
  `dev: "sonnet"`**; reserve `"opus"` for the hardest stories or when you have weekly-Opus budget.
- **Fully overridable** via `loop.json` `bmad.models`; set all to `""` to restore strict PS parity.
- **Recommended starting config (quota-tight):**
  ```jsonc
  { "bmad": { "models": {
      "create": "sonnet", "dev": "sonnet", "review": "sonnet",
      "smoke": "sonnet", "retro": "sonnet", "decider": "haiku" } } }
  // bump "dev" -> "opus" for the hardest stories when Opus weekly budget allows.
  ```

### 5.2 Per-phase token/cache telemetry — additive `token-usage` event
- Emitted centrally in `ResilientRunner` after every productive run, tagged by **phase + story +
  model**, carrying `input / output / cacheRead / cacheCreation / hitRatio / warm` and **cumulative**
  token counts (`cumInput`, `cumCacheRead`, …). Costs **zero** extra tokens (the data is already in
  claude's `--output-format json` response).
- It's an **additive** event (like `metrics`): not in the golden corpus, ignored by older reducers.
- **How to read the burn from `log.jsonl`:**
  ```bash
  # total input tokens by phase + model (the real Max-plan meter):
  grep '"event":"token-usage"' .loop/log.jsonl \
    | jq -r '[.phase,.model,.input,.cacheRead] | @tsv'
  # ★ if cacheRead is ~0 everywhere, caching is cold/broken — that's the leak.
  ```

### 5.3 Recall-biased code-review + quality dev-story prompt (see §6).

### 5.4 Single-pass review + smoke modes — collapse the cold-start fan-out
The big surprise in the audit: the loop runs each *phase* ~once (like you do by hand), but the
headless `claude -p` protocol **can't pause mid-process to wait for an answer**, so the review/retro
Q&A loops fragment into many **cold-start processes** — reviewer emits `QUESTION:` → process exits →
a separate decider process answers → reviewer `--resume`s as *another* process. One review with N
findings ≈ **(2N+1) cold processes**, each re-loading CLAUDE.md + MCP + skills.

- **`bmad.reviewMode: "single-pass"`** — folds the decider's PATCH/DEFER/DISMISS principles into the
  reviewer's own prompt so it decides + applies every finding in **one warm process** (no `QUESTION:`
  round-trips, no separate decider spawns). Mirrors a human reviewing-and-fixing in one session;
  ~(2N+1) processes → **1**. (Trade-off: you lose the per-finding decision log / human-override hook.)
- **`bmad.smokeMode: "single-pass"`** — runs browser-smoke as **one process** that does all
  open/test/fix/re-test internally (bounded by the wall-clock timeout) instead of cold-re-spawning
  on FAIL.
- Both **default to today's behavior** (`"qa"` / `"iterative"`) for parity; both round-trip through
  `loop.json`, the `--review-mode` / `--smoke-mode` CLI flags, and the checkpoint resume command.
- **Why this beats a "warm-session" rewrite:** the only *other* way to kill the fan-out is to keep one
  persistent `claude` process alive across turns (`claude -p --input-format stream-json`, or the Agent
  SDK's `ClaudeSDKClient`). But that's more complex, the stream-json input mode is officially
  undocumented, and the SDK-with-subscription-OAuth path is a ToS gray area — whereas single-pass is a
  pure prompt/orchestration change on the sanctioned `claude -p` path, and after it there's essentially
  no multi-turn-in-one-process left in the per-story pipeline to warm.

### 5.5 Per-phase reasoning effort (`--effort`) + the `loop.json` config surface
The verified `claude --effort <low|medium|high|xhigh|max>` flag now threads per phase, exactly like
`--model` (empty = inherit your CC default → no flag → byte-parity). `loop-bmad` also grew a
**`--loop-json`** flag so per-phase `models` / `effort` / `gateStages` / `reviewMode` / `smokeMode`
are configurable from a file (CLI flags stay authoritative for run-location + operational toggles).

```jsonc
// my-loop.json — run with:  loop-bmad --project-root <repo> --state-dir .loop --loop-json my-loop.json
// QUALITY-FIRST (match a 1M-context-Opus-xhigh hand-workflow on every work phase). Affordable in
// the loop ONLY because single-pass collapses review (~16 procs) + smoke (3) to 1 process each.
{
  "bmad": {
    "models": {                          // "" = inherit CC default; aliases or full ids both work
      "create": "claude-opus-4-8[1m]", "dev": "claude-opus-4-8[1m]",
      "review": "claude-opus-4-8[1m]", "smoke": "claude-opus-4-8[1m]",
      "retro": "claude-opus-4-8[1m]", "decider": "sonnet"
    },
    "effort": {                          // "" = inherit. KEEP decider low — xhigh hurts a bounded choice.
      "create": "xhigh", "dev": "xhigh", "review": "xhigh",
      "smoke": "xhigh", "retro": "xhigh", "decider": "low"
    },
    "reviewMode": "single-pass",
    "smokeMode": "single-pass",
    "retroMode": "single-pass"
  }
}
```

> Model strings: `claude-opus-4-8[1m]` is this session's exact 1M-context Opus model ID; if your
> Claude Code `/model` picker shows a different token for it, use that. `opus`/`sonnet`/`haiku`
> aliases also work. **The one firm rule: never set `decider` to `xhigh`** — high effort makes a
> model over-deliberate a bounded one-shot decision (more tokens, *worse* decisiveness); the *model*
> tier (haiku vs sonnet) is a free quality choice, the *effort* is the guardrail.
> **Decider is inert when every phase is single-pass:** `reviewMode`/`retroMode: "single-pass"` fold
> the decision into the reviewer/facilitator itself, so `review_decider`/`retro_decider` are never
> called — the `decider` model/effort only takes effect if you switch a phase back to `"qa"`.
> **Leaner alternative (more stories per window):** drop `create` + `smoke` to `"sonnet"` /
> `"medium"` — Opus-xhigh buys little on story scaffolding or tool-driven browser verification (the
> external gate is the real correctness check there); keep the full bar on **dev + review**.

---

## 6. Designing an *efficient AND quality-output-first* loop

*Sources: `loop-engineering.md`, `docs/capabilities.md`, the code, Anthropic's code-review +
long-running-agent guidance, and external agentic-coding best practice.*

### What the loop already gets right (don't rebuild)
The **external exit-code gate is truth** (never the model's word); **pass-count floor** at every
phase boundary blocks the crudest reward-hack (deleting tests); **frozen ACs** feed the smoke prompt;
**fresh context** per phase. This is a genuinely quality-first skeleton.

### The headline gap
The strongest quality machinery — the **anti-false-green verifier**, **hash-lock**,
**compact-feedback**, **held-out split** — is built in `core.py` (the generic loop) but **default-off
and never wired into the BMAD applied track**, which today protects itself with the **count-floor
alone**. The count-floor catches *deleted* tests; it does **not** catch *shallow* tests or a review
that rubber-stamps a spec violation that still compiles. LLM self-review has a documented **leniency
bias**, and the generator + reviewer "fail in correlated ways."

### The quality-first, token-aware recipe (minimal correctness-per-token)
Adds **at most one cheap (Haiku) call per story**; everything else is zero extra model tokens.

| Lever | What it buys | Token cost | Priority |
|---|---|---|---|
| **Adversarial verify-before-merge** (Haiku, diff-only, refute the story's *frozen* ACs, **block PR on refute**) | The **#1 quality lever** — the only thing that breaks the generator↔reviewer correlated false-green. Sees a frozen contract, told to *refute*, over-weights "DO NOT" criteria. | **+1 Haiku call/story**, after work is already green (rarely re-triggers) | **High** |
| **Recall-biased code-review** (reviewer reports *every plausible correctness/security/AC* finding, tagged `[P1|P2|P3]`; the cheap Haiku **decider filters** PATCH/DEFER/DISMISS) | Matches Anthropic's own production reviewer (report→verify→filter, <1% wrong). Recall up, precision preserved. | Bounded by the ≤8-turn cap; extra turns are **Haiku** | **High — shipped (§6.1)** |
| **Per-phase `--effort`** (dev `high`→`xhigh` on a retry; review `high`; create/smoke `medium`; retro/decider `low`) | Reasoning depth where it cuts red iterations; *low* where high effort would only over-deliberate. Often **net-cheaper** (fewer turns). | Flag only — cache-safe | **High** |
| **Run-quality metrics for BMAD** | You can't tune what you don't measure (cost/iters-to-green, regression rate). | 0 model tokens | Medium |
| **Cheap plan-gate before dev-story** (Haiku, single-turn: are the ACs testable/atomic? `BLOCKED:` → halt) | Insurance against the most expensive failure: Opus grinding for hours on an ambiguous spec. | +1 Haiku call/story (early-exit on easy stories) | Medium |
| **Gate `fail-fast`** (`run_gate` runs *all* stages unconditionally today — short-circuit after the first non-zero) | Stops paying for a heavy `bun run test` after `lint` already failed. Keep the floor keyed off the test-stage count. | 0 model tokens (build time saved) | Medium |
| **Extend hash-lock into BMAD** | Catches *edit-in-place* of an assertion (count stays constant) that the floor misses. | 0 model tokens | Medium |

**Leave OFF on this budget:** **mutation audit** (re-runs the full `bun` gate up to 8× — worst ROI on
a build-heavy, token-starved loop), **cross-run lessons memory** (redundant with the **epic
retrospective**, and it inflates every prompt prefix), **held-out test split** (needs a hidden
human-authored suite the TDD-writes-its-own-tests flow doesn't naturally have).

**Tensions to decide:** (T1) skip held-out for BMAD — the count-floor + adversarial verifier give most
of the protection at none of the test-authoring cost. (T2) keep the adversarial check to **one refute
pass**, not a multi-round actor-critic loop — depth beyond one round isn't worth it on a token budget.
(T3) the plan-gate taxes *easy* stories one Haiku call — worth it because one saved Opus dev-grind pays
for hundreds.

### 6.1 Shipped quality prompt changes
- **Code-review** (`_CODE_REVIEW_PROMPT_TEMPLATE`): the reviewer is now told to **surface every
  plausible correctness / security / AC-violation finding** (recall-biased, don't pre-suppress
  uncertain ones), **one per turn, most-severe-first, tagged `[P1|P2|P3]`**, and let the downstream
  Haiku decider filter — recall up, precision preserved, capped by `max_review_turns`.
- **dev-story** (`_DEV_STORY_PROMPT`): leads with the harness-paper essentials — implement against the
  story's acceptance criteria, **run continuously to green**, **commit progress with descriptive
  messages**, and **never weaken, skip, or delete a test**.

---

## 7. Roadmap (prioritized, ready to implement)

Each is **additive / default-off** (preserves the 375-test golden parity) and CLI/subscription-only.

1. ~~**`--effort` wiring**~~ — **DONE (§5.5).** `effort` threads through `AgentRunner.run` → all 3
   backends (`ClaudeRunner` emits `--effort`; `aider`/`codex` accept-and-ignore) → per-phase
   `BmadConfig.effort` map + `effort_for(phase)` → CLI `--loop-json`. Default empty = byte-parity.
2. **Adversarial verify-before-merge** — port `core.py:_run_verify` into the driver **between smoke and
   `pr.create_pr`**, sourcing the contract from `story_acs(story_text)` and the diff from
   `story_meta().baseline`, on the **Haiku** tier; refute → block the PR + actionable handoff. Add a
   `bmad.verify` config block. *(biggest quality win)*
3. **BMAD run-quality metrics** — teach `compute_metrics` to read the `bmad-stop` event (or emit a
   parallel `stop`-shaped event); flag `bmad.metrics.emit`.
4. **Cheap plan-gate** before dev-story (Haiku, single-turn, `BLOCKED:`→halt); additive `plan-check`
   event.
5. **Gate `fail-fast`** — opt-in `gate.fail_fast` that `break`s `run_gate` after the first non-zero
   stage; keep the pass-count floor keyed off the test stage.
6. **Quota survival hardening** (sink #4/#8) — don't blind-re-run a near-budget unbounded phase;
   probe with a minimal no-MCP invocation; consider a shorter re-probe clamp (trades probe tokens for
   faster resume — see §8).
7. **Extend hash-lock into BMAD** (sink, §6 table).
8. **Evaluate `ClaudeSDKClient`** for the review/retro Q&A loops (warm session, fewer cold starts) —
   *after* confirming the subscription-OAuth ToS position (§4).

---

## 8. "The limits reset but the loop didn't resume" — diagnosis

**By design, `resume = re-run`.** The loop does **not** relaunch itself. When quota survival gives up
(after `max_quota_waits` cycles) — or any phase hands off — the process **exits cleanly**, having
written `checkpoint.json` (with a ready-to-run `resume` command) and a `bmad`-`stop` log line. A
**supervisor** must re-run it: the **Orrery app's "Reignite"** (which uses the checkpoint's resume
string), a shell `while` wrapper, or cron. If nothing is supervising, the loop stays stopped even
after quota frees — that is almost certainly what you saw.

**While quota is exhausted but the process is *alive*,** `survive()` sleeps to the reset and re-probes
each cycle (clamped to ≤6 h per sleep), so it **will** auto-resume within ~6 h of the *actual* clear.
It does **not** hang permanently: once quota frees, the next `claude -p ok` probe returns clean →
`quota-resume` → the phase retries. The one rough edge: if the limit was attributed to a *later*
(weekly) reset, it can over-wait up to ~6 h past a 5-h clear before the next re-probe — it self-corrects,
it's just slow.

**To check your actual run:**
```bash
tail -n 5 <state-dir>/log.jsonl      # last event: "stop"/"bmad stop" => it EXITED (re-run/Reignite)
                                     #             "quota-wait"        => alive, mid-sleep (≤6h)
cat <state-dir>/checkpoint.json      # the `resume` field is the exact command to relaunch
```
**Fix:** run the `checkpoint.json` `resume` command (or click **Reignite** in Orrery). For unattended
operation, wrap the launch in a supervisor that re-runs on a non-zero/quota exit — the durable state
(sprint-status + git + checkpoint) makes re-run safe and lossless.

---

## 9. Continuous runs, stopping cleanly, and smoke targeting

**Continuous operation.** The loop runs story-after-story until the backlog is empty — *if* each
story **merges**. The post-smoke flow is: smoke → push → open PR → **merge → next story**. Only a
*confirmed* merge continues; two things stop it:

- **`--no-merge`** deliberately stops after the PR (rc 0). It **cannot** continue — the next story
  branches off the merge-base, so it can't inherit an un-merged PR. For continuous multi-story runs,
  **drop `--no-merge`.**
- **A merge that didn't land** — on a branch-protected base, `gh pr merge` exits 0 but the PR sits
  **QUEUED** behind required checks, so `pr_state != "MERGED"` and the driver halts. New opt-in
  **`mergeWaitSec`** (loop.json) / `--merge-wait-sec N` polls the PR up to N seconds for the merge to
  land before halting — set it (e.g. `120`) on a protected base so the loop rolls into the next story
  instead of stopping. Default `0` = strict immediate halt (parity).

Every halt now prints an actionable **`[HALT] <why>`** + the exact **`resume:`** command (the
checkpoint's `_resume_command`, which round-trips your models/effort/modes/flags), so a stop is never
a mystery.

**Choosing whether & where to stop.** The loop never gets killed mid-step — request a *cooperative*
stop and it finishes the in-flight work, commits, checkpoints, and exits 0:
```bash
loop-stop --state-dir .loop --after-story   # stop once the CURRENT story is merged
loop-stop --state-dir .loop --now           # stop at the next safe phase (after dev / review / smoke)
loop-stop --state-dir .loop --status        # is a stop pending? where's the last checkpoint?
loop-stop --state-dir .loop --cancel        # changed your mind — keep going
```
Honored at these safe boundaries: **between stories, after dev-story, after code-review, after
browser-smoke, and right after a story's merge** (the last two are new — so `--after-story` halts the
instant the story completes, not a whole story later). `loop-bmad` now prints this hint at startup so
it's discoverable, and the `[STOPPED]` message echoes the resume command.

**Smoke verifies *this* story's implementation.** Browser-smoke was AC-aware but only got the ACs +
URL — never the story's diff — so it could pass as a generic health check. It now also receives the
story's **changed files** (computed from `baseline_commit`) and their likely **routes**, so it drives
the surfaces *this* story actually changed; and it records a structured **`SMOKE_ACS:`** line parsed
into `verifiedAcs` / `deferredAcs` on the `smoke-iter` event (which ACs were driven in-browser with
evidence vs deferred to the test suite). Inspect it:
```bash
grep '"event":"smoke-iter"' .loop/log.jsonl | jq '{passed, verifiedAcs, deferredAcs, verdict}'
```
(All additive: no `baseline_commit` → untargeted-but-AC-aware prompt as before; no `SMOKE_ACS:` line →
the fields are omitted. The Orrery visualizer can surface `verifiedAcs`/`deferredAcs` as a follow-up.)

## Appendix: confidence & sources

- **Official / high-confidence:** the 5-h + two-weekly limit structure; current "all-models +
  Sonnet-only" weekly split; token-based, Opus-heavier metering; **cache reads don't count against the
  rate limit**; all cache multipliers/TTL/scoping; `claude -p` still on the subscription (June-15 split
  **paused**); `--effort` / `--model` / `--append-system-prompt[-file]` / `--max-budget-usd` flags;
  `t3code` spawns the local binary; Anthropic's report-then-filter review + long-running-agent
  guidance.
- **Uncertain / don't harden:** exact current per-window/per-week token budgets; the precise Opus
  budget-weight (~2× is an estimate); whether the SDK's spawn-the-bundled-CLI path counts as "the CLI"
  (allowed) vs "the SDK" (API-key-required) for subscription OAuth — **verify before relying on it**.
- **Verify live in Settings → Usage** for your account's actual 5-h/weekly numbers and the exact
  Friday Asia/Colombo reset.

Key sources: support.claude.com (Max plan; usage-limit best practices; models/usage in Claude Code);
TechCrunch (weekly limits); platform.claude.com (prompt caching); code.claude.com (headless; CLI
reference; env vars; sessions); anthropic.com (Opus 4.5 effort economics; code-review; effective
harnesses for long-running agents); github.com/pingdotgg/t3code.
