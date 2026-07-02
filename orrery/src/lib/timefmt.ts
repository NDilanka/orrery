// Small wall-clock formatting helpers shared by Hud.svelte / Cosmos.svelte (Task 4:
// run.startedAt / run.lastEventAt display — PROTOCOL §3/§4 rule 8).
//
// Defensive against synthetic/placeholder timestamps: the reducer's `t` is a real ms-since-epoch
// per PROTOCOL, but test/replay fixtures stamp it as a synthetic line-index×1000 (so it reads as
// 1970-01-01 + a few seconds) until every caller is wired to pass a real wall clock. Anything
// before SANE_EPOCH_MS is treated as "no meaningful time" so the UI never shows a multi-decade
// duration like "running 487364h" against a fixture.
const SANE_EPOCH_MS = Date.parse('2000-01-01T00:00:00Z');

function parseSane(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t) || t < SANE_EPOCH_MS) return null;
  return t;
}

/** "HH:MM" in local time, or null when `iso` is absent/unreliable. */
export function fmtClock(iso: string | null | undefined): string | null {
  const t = parseSane(iso);
  if (t == null) return null;
  const d = new Date(t);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** A duration in ms → "Xh Ym" (or "Ym" under an hour). */
export function fmtDuration(ms: number): string {
  const totalMin = Math.max(0, Math.round(ms / 60000));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** Coarse "Xm ago" / "just now" relative to `now`, or null when `iso` is absent/unreliable. */
export function fmtRelative(iso: string | null | undefined, now: number = Date.now()): string | null {
  const t = parseSane(iso);
  if (t == null) return null;
  const diffSec = Math.max(0, Math.round((now - t) / 1000));
  if (diffSec < 45) return 'just now';
  const min = Math.round(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}
