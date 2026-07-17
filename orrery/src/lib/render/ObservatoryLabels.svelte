<script lang="ts">
  // ObservatoryLabels — an absolutely-positioned, pointer-events:none HTML overlay
  // that sits OVER the Pixi canvas (in Observatory's .ofield) and makes the scene
  // legible in isolation. It is deliberately restrained / low-contrast so it never
  // fights the rendered instrument, and it works in Planetarium (Tier-1) too.
  //
  // It draws:
  //   · a small caption just under the star — cumulative spend + the per-minute
  //     rate (tabular figures, --text-meta);
  //   · a small label (story key + status glyph) near EVERY orbit body (Task 3), decluttered —
  //     faded/shrunk when not current, and skipped in a dense ring (>12 bodies) unless the body
  //     is current or needs attention (failed / claimed-but-unverified);
  //   · the CURRENT body's label gets the full treatment plus a ◌/✓ claimed-vs-verified trust
  //     glyph prefix (Task 2 — the product's core trust signal, promoted off the tiny planet ring);
  //   · an optional tiny 50/80/100% tick label on the cost-horizon ring.
  //
  // Positions arrive pre-computed/THROTTLED from the rAF (so we don't thrash
  // reactivity). Under reduced motion we place statically — no transitions.

  type BodyLabel = {
    key: string;
    x: number;
    y: number;
    status: string;
    trust: 'verified' | 'unverified' | null;
    current: boolean;
    // wave U2 Task 3: this is runStore.auditTargetKey — the retired Observatory
    // "lighthouse" (a tower + sweeping beam) used to be the only signal that this
    // claimed-green body was being audited; a pulsing "verifying…" label replaces it.
    auditing: boolean;
  };
  type Labels = {
    cumUsd: number;
    ratePerMin: number;
    star: { x: number; y: number };
    bodies: BodyLabel[];
    horizonPct: number | null;
  };

  let { labels, reduced }: { labels: Labels; reduced: boolean } = $props();

  function fmtUsd(n: number): string {
    return '$' + (n ?? 0).toFixed(2);
  }

  // a small, non-hue-alone status glyph per work-item lifecycle status (distinct from the trust
  // glyph ◌/✓, which is about claimed-vs-verified, not lifecycle stage).
  function statusGlyph(status: string): string {
    switch (status) {
      case 'done':
        return '●';
      case 'review':
        return '◑';
      case 'in-progress':
        return '◐';
      case 'blocked':
        return '⊘';
      case 'failed':
        return '✕';
      case 'ready':
        return '○';
      default:
        return '·'; // backlog
    }
  }

  // the horizon tick reads at its nearest milestone band (50 / 80 / 100%)
  const horizonBand = $derived.by<{ pct: number; cls: string } | null>(() => {
    const p = labels.horizonPct;
    if (p == null || p < 50) return null;
    if (p >= 100) return { pct: 100, cls: 'crit' };
    if (p >= 80) return { pct: 80, cls: 'warn' };
    return { pct: 50, cls: 'note' };
  });

  // ── M2.8: greedy candidate-anchor label placement ─────────────────────────
  // Every body label gets tried at 4 candidate anchors around its planet (below,
  // above, right, left) against the AABBs already claimed this frame, in priority
  // order (current > failed/unverified > rest) so the labels that matter most get
  // first pick. First non-overlapping anchor wins; a body that finds nothing free
  // is dimmed to a dot marker instead of stacking illegible text (same "drop
  // rather than overlap" philosophy as the upstream dense-ring declutter in
  // Observatory.svelte, just applied one layer closer to the actual pixels).
  //
  // Anchor hysteresis: once a body has an anchor, we keep it next frame unless it
  // now ACTUALLY collides — this is what stops labels flickering between "above"
  // and "right" as bodies drift a pixel at a time. The cache is a plain (non-
  // reactive) Map rebuilt each frame from itself, so a body that stops being
  // labelled (dense-ring drop, leaves the scene) can't leak forever.
  type Anchor = 'below' | 'above' | 'right' | 'left';
  type Rect = { l: number; t: number; r: number; b: number };
  type Laid = {
    key: string;
    b: BodyLabel;
    rect: Rect;
    dropped: boolean;
    leader: { x1: number; y1: number; len: number; angleRad: number } | null;
  };

  const ANCHOR_ORDER: Anchor[] = ['below', 'above', 'right', 'left'];
  // BODY_R: assumed on-canvas planet radius. Observatory draws planets at
  // `pr = 5 + (merged ? 1.5 : 0)` (not threaded through the labels prop — see the
  // file-header note on the DOM-overlay/Pixi-canvas split) — 5px is the common
  // case, used here purely as a label-layout constant, not a coupling to canvas
  // internals. GAP is chosen so BODY_R + GAP == 12, the exact rim-to-label gap the
  // old fixed "above" placement used — the common uncontested case renders
  // pixel-identical to before; only actual collisions change anything.
  const BODY_R = 5;
  const GAP = 7;
  const AABB_PAD = 3; // breathing room between placed boxes
  const DISPLACE_MIN = BODY_R * 1.5; // leader-line threshold (plan: "~1.5× body radius")

  let lastAnchor = new Map<string, Anchor>();

  function labelPriority(b: BodyLabel): number {
    if (b.current) return 0;
    if (b.status === 'failed' || b.trust === 'unverified') return 1;
    return 2;
  }

  // rough monospace box estimate — cheap and deterministic; the rendered box is
  // pinned to this exact width (see template) so the collision math never lies
  // about what's actually on screen.
  function estimateSize(b: BodyLabel): { w: number; h: number } {
    const charW = b.current ? 6.4 : 5.6;
    const h = b.current ? 16 : 13;
    let chars = b.key.length + 2; // status glyph + gaps
    if (b.trust) chars += 2; // trust glyph prefix
    if (b.auditing) chars += 11; // "verifying…"
    const cap = b.current ? 168 : 96; // matches the existing per-tier max-width
    return { w: Math.min(cap, chars * charW), h };
  }

  function anchorRect(anchor: Anchor, x: number, y: number, w: number, h: number): Rect {
    switch (anchor) {
      case 'below':
        return { l: x - w / 2, t: y + BODY_R + GAP, r: x + w / 2, b: y + BODY_R + GAP + h };
      case 'above':
        return { l: x - w / 2, t: y - BODY_R - GAP - h, r: x + w / 2, b: y - BODY_R - GAP };
      case 'right':
        return { l: x + BODY_R + GAP, t: y - h / 2, r: x + BODY_R + GAP + w, b: y + h / 2 };
      case 'left':
        return { l: x - BODY_R - GAP - w, t: y - h / 2, r: x - BODY_R - GAP, b: y + h / 2 };
    }
  }
  function overlaps(a: Rect, b: Rect): boolean {
    return a.l - AABB_PAD < b.r && a.r + AABB_PAD > b.l && a.t - AABB_PAD < b.b && a.b + AABB_PAD > b.t;
  }
  // the point on the box nearest the body — the anchor's "attach" edge midpoint
  function anchorPoint(anchor: Anchor, r: Rect): { x: number; y: number } {
    switch (anchor) {
      case 'below':
        return { x: (r.l + r.r) / 2, y: r.t };
      case 'above':
        return { x: (r.l + r.r) / 2, y: r.b };
      case 'right':
        return { x: r.l, y: (r.t + r.b) / 2 };
      case 'left':
        return { x: r.r, y: (r.t + r.b) / 2 };
    }
  }

  // a body's status hue for its leader line — reuses the same two-tier
  // status-*-core tokens the rest of the app uses for status color (plan §M0.1).
  // M4.5: 'blocked' moved from the err/red bucket to warn/amber (matches Observatory's
  // planetPair() — blocked means "a human needs to look at this", not "this crashed"; only
  // 'failed' stays red). Order matters: failed (red) checked first, then blocked/unverified
  // (both amber) before the calmer done/running/idle grays.
  function leaderColor(b: BodyLabel): string {
    if (b.status === 'failed') return 'var(--status-err-core)';
    if (b.status === 'blocked' || b.trust === 'unverified') return 'var(--status-warn-core)';
    if (b.status === 'done') return 'var(--status-ok-core)';
    if (b.status === 'in-progress' || b.status === 'review') return 'var(--status-run-core)';
    return 'var(--status-idle-core)';
  }

  // PURE layout pass: computes both the placed labels AND the next-frame anchor map, WITHOUT
  // mutating `lastAnchor` (the hysteresis memory). Committing that memory is a side effect, so it
  // moved to the $effect below — a $derived must not write shared state or it recomputes with a
  // stale/partial view under Svelte's batching. Reading `lastAnchor` here is fine: it's a plain
  // let (no reactive edge), so this pass sees last frame's decision exactly as before.
  const laidOut = $derived.by<{ items: Laid[]; nextAnchor: Map<string, Anchor> }>(() => {
    const bodies = [...labels.bodies].sort((a, c) => labelPriority(a) - labelPriority(c));
    const placed: Rect[] = [];

    // pre-occupy the star caption / horizon tick footprints so body labels steer
    // clear of them too — approximate boxes, cheap, no measurement needed.
    placed.push({ l: labels.star.x - 70, t: labels.star.y + 18, r: labels.star.x + 70, b: labels.star.y + 36 });
    if (horizonBand) {
      placed.push({ l: labels.star.x - 22, t: labels.star.y - 48, r: labels.star.x + 22, b: labels.star.y - 34 });
    }

    const nextAnchor = new Map<string, Anchor>();
    const out: Laid[] = [];

    for (const b of bodies) {
      const { w, h } = estimateSize(b);
      let chosen: Anchor | null = null;
      let rect: Rect | null = null;

      const prev = lastAnchor.get(b.key);
      if (prev) {
        const r = anchorRect(prev, b.x, b.y, w, h);
        if (!placed.some((p) => overlaps(r, p))) {
          chosen = prev;
          rect = r;
        }
      }
      if (!chosen) {
        for (const a of ANCHOR_ORDER) {
          const r = anchorRect(a, b.x, b.y, w, h);
          if (!placed.some((p) => overlaps(r, p))) {
            chosen = a;
            rect = r;
            break;
          }
        }
      }

      if (chosen && rect) {
        nextAnchor.set(b.key, chosen);
        placed.push(rect);
        let leader: Laid['leader'] = null;
        if (chosen !== ANCHOR_ORDER[0]) {
          const defRect = anchorRect(ANCHOR_ORDER[0], b.x, b.y, w, h);
          const dp = anchorPoint(ANCHOR_ORDER[0], defRect);
          const cp = anchorPoint(chosen, rect);
          const dist = Math.hypot(dp.x - cp.x, dp.y - cp.y);
          if (dist > DISPLACE_MIN) {
            // straight line from the body rim (toward the label) to the label's nearest corner
            const corners = [
              { x: rect.l, y: rect.t },
              { x: rect.r, y: rect.t },
              { x: rect.l, y: rect.b },
              { x: rect.r, y: rect.b },
            ];
            let best = corners[0];
            let bestD = Infinity;
            for (const c of corners) {
              const d = Math.hypot(c.x - b.x, c.y - b.y);
              if (d < bestD) {
                bestD = d;
                best = c;
              }
            }
            const angleRad = Math.atan2(best.y - b.y, best.x - b.x);
            leader = {
              x1: b.x + Math.cos(angleRad) * BODY_R,
              y1: b.y + Math.sin(angleRad) * BODY_R,
              len: Math.hypot(best.x - b.x, best.y - b.y) - BODY_R,
              angleRad,
            };
          }
        }
        out.push({ key: b.key, b, rect, dropped: false, leader });
      } else {
        // no candidate anchor is free — dim to a dot marker rather than stack text
        out.push({ key: b.key, b, rect: anchorRect('below', b.x, b.y, w, h), dropped: true, leader: null });
      }
    }

    return { items: out, nextAnchor };
  });

  // Commit the hysteresis memory as a side effect (never inside the derived above). Runs after
  // each layout pass; `lastAnchor` is a plain let, so this write creates no reactive loop back
  // into the derived — it simply parks the decision for the next frame to read.
  $effect(() => {
    lastAnchor = laidOut.nextAnchor;
  });
</script>

<div class="olabels" class:reduced aria-hidden="true">
  <!-- spend + rate caption, pinned just under the star core -->
  <div
    class="cap num"
    style="left:{labels.star.x}px; top:{labels.star.y}px;"
  >
    <span class="usd">{fmtUsd(labels.cumUsd)}</span>
    {#if labels.ratePerMin > 0}
      <span class="rate">{fmtUsd(labels.ratePerMin)}/min</span>
    {/if}
  </div>

  <!-- every orbit body, named at its planet via the greedy-anchor placement above —
       current gets the full/undimmed treatment (+ the claimed-vs-verified trust glyph),
       others are small/faded (Task 3 declutter); a body that lost the anchor contest
       renders as a dot instead of stacking illegible text (M2.8) -->
  {#each laidOut.items as item (item.key)}
    {#if item.dropped}
      <div
        class="dot"
        class:current={item.b.current}
        style="left:{item.b.x}px; top:{item.b.y}px;"
        title={item.b.key}
        aria-hidden="true"
      ></div>
    {:else}
      <div
        class="body mono"
        class:current={item.b.current}
        class:other={!item.b.current}
        style="left:{item.rect.l}px; top:{item.rect.t}px; width:{item.rect.r - item.rect.l}px;"
        title={item.b.auditing ? `${item.b.key} — verifying…` : item.b.key}
      >
        {#if item.b.current && item.b.trust}
          <span class="trust {item.b.trust}" aria-hidden="true">{item.b.trust === 'verified' ? '✓' : '◌'}</span>
        {/if}
        <span class="bkey">{item.b.key}</span>
        {#if item.b.auditing}
          <span class="verifying">verifying…</span>
        {/if}
        <span class="bglyph" aria-hidden="true">{statusGlyph(item.b.status)}</span>
      </div>
      {#if item.leader}
        <div
          class="leader"
          style="left:{item.leader.x1}px; top:{item.leader.y1}px; width:{item.leader.len}px;
            transform: rotate({item.leader.angleRad}rad); background:{leaderColor(item.b)};"
          aria-hidden="true"
        ></div>
      {/if}
    {/if}
  {/each}

  <!-- a tiny horizon milestone tick (50/80/100%) -->
  {#if horizonBand}
    <div
      class="horizon num {horizonBand.cls}"
      style="left:{labels.star.x}px; top:{labels.star.y}px;"
    >
      {horizonBand.pct}%
    </div>
  {/if}
</div>

<style>
  .olabels {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: var(--z-labels);
    /* low contrast so the labels read as instrument annotation, not chrome */
    font-family: var(--font-grotesk);
  }

  /* spend + rate, centred under the star core */
  .cap {
    position: absolute;
    transform: translate(-50%, 18px);
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    white-space: nowrap;
    font-size: var(--text-meta, 11px);
    transition: left var(--dur-mid) var(--ease-standard),
      top var(--dur-mid) var(--ease-standard);
  }
  .cap .usd {
    /* M4.4/M4.5: demoted to em-faint — the HUD is now the canonical spend readout, so this
       on-canvas caption is a quiet echo, not a competing value (plan §5 M4.4 "kill duplicate
       emphasis: canvas spend caption drops to em-faint"). `--text-faint` is the semantic alias
       for `--em-faint` (tokens.css Tier 2) — same tier this file's other decorative text uses.
       Was var(--brass) at --text-sm. */
    color: var(--text-faint);
    font-size: var(--text-sm);
    letter-spacing: 0.02em;
  }
  .cap .rate {
    /* M4.5: matched to .usd's new em-faint tier — previously --text-meta (em-low) was
       actually BRIGHTER than usd's old brass, which would have inverted the hierarchy (a
       secondary rate outshining the primary spend figure) once usd was demoted. Both live in
       the same faint tier now; .usd's larger font-size is what still marks it primary. */
    color: var(--text-faint);
    font-size: var(--text-2xs);
  }

  /* every orbit body's key, placed by the greedy anchor algorithm above (M2.8) —
     left/top/width are the exact AABB the placement math reserved, so there's no
     drift between what was collision-checked and what's on screen. The KEY is the
     child that ellipsizes (min-width:0 + hidden overflow) so the trailing
     "verifying…" pulse / status glyph always survive a long story key. Bare-text
     labels (no panel/background here by design) get a two-layer shadow instead of
     a background chip, so they stay legible over any part of the canvas. */
  .body {
    position: absolute;
    display: inline-flex;
    align-items: baseline;
    gap: 3px;
    max-width: 168px;
    overflow: hidden;
    white-space: nowrap;
    letter-spacing: 0.08em;
    text-shadow: 0 1px 2px rgb(0 0 0 / 0.8), 0 0 6px rgb(0 0 0 / 0.5);
    transition: left var(--dur-mid) var(--ease-standard),
      top var(--dur-mid) var(--ease-standard),
      opacity var(--dur-mid) var(--ease-standard);
  }
  .body .bkey {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .body .trust,
  .body .verifying,
  .body .bglyph {
    flex: none;
  }
  /* current body: full-size, undimmed — the one label that matters most right now */
  .body.current {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    opacity: 1;
  }
  /* every other body: small + faded (declutter) — a quiet map pin, not a competing label */
  .body.other {
    font-size: var(--text-2xs);
    color: var(--text-faint);
    opacity: 0.55;
    max-width: 96px;
  }
  .body .bglyph {
    opacity: 0.85;
  }
  /* claimed-vs-verified trust glyph (Task 2), current body only — paired with the VERIFIED/
     UNVERIFIED word elsewhere (Hud/Cosmos); here it's a compact glyph-only prefix. */
  .body .trust {
    font-size: var(--text-xs);
  }
  .body .trust.unverified {
    color: var(--status-warn-core);
  }
  .body .trust.verified {
    color: var(--status-ok-core);
  }
  /* the audit-in-flight signal (wave U2 Task 3, replaces the Observatory lighthouse) —
     a cold auditor-white pulse, subtle so it never competes with the trust glyph. */
  .body .verifying {
    color: var(--auditor-white);
    font-size: var(--text-2xs);
    opacity: 0.85;
    animation: verifyPulse 2.2s ease-in-out infinite;
  }
  @keyframes verifyPulse {
    0%, 100% { opacity: 0.85; }
    50% { opacity: 0.35; }
  }

  /* horizon milestone tick — placed top-of-star, recessed */
  .horizon {
    position: absolute;
    transform: translate(-50%, -34px);
    font-size: var(--text-2xs);
    letter-spacing: 0.1em;
    color: var(--text-faint);
    transition: left var(--dur-mid) var(--ease-standard),
      top var(--dur-mid) var(--ease-standard);
  }
  /* cost-horizon ladder (cross-wave contract, matches Hud.svelte): below 80% of ceiling is
     NEUTRAL — the 50% milestone ('note') is informational, not an alert, so it inherits the
     same --text-faint the base .horizon rule already uses instead of overriding to amber.
     80-99% ('warn') earns --horizon-rose (now itself var(--status-warn-core), i.e. amber —
     see tokens.css) and only >=100% ('crit') earns red. */
  .horizon.warn { color: var(--horizon-rose); }
  .horizon.crit { color: var(--crimson); }

  /* M2.8: a body that lost every anchor contest this frame (rare — a local
     traffic jam of same-priority labels) dims to a point instead of stacking
     illegible text on top of a neighbor's label. */
  .dot {
    position: absolute;
    width: 4px;
    height: 4px;
    margin: -2px;
    border-radius: 50%;
    background: var(--text-faint);
    opacity: 0.6;
    transition: left var(--dur-mid) var(--ease-standard),
      top var(--dur-mid) var(--ease-standard);
  }
  .dot.current {
    background: var(--text-meta);
    opacity: 0.9;
  }

  /* M2.8: leader line — only rendered when a label was displaced off its default
     anchor by more than ~1.5x body radius, so the reader can still trace it back
     to its planet. A 1px hairline in the body's status hue, kept faint. */
  .leader {
    position: absolute;
    height: 1px;
    width: 0;
    transform-origin: 0 50%;
    opacity: 0.35;
  }

  /* reduced motion: place statically, no easing of position */
  .olabels.reduced .cap,
  .olabels.reduced .body,
  .olabels.reduced .dot,
  .olabels.reduced .horizon {
    transition: none;
  }
</style>
