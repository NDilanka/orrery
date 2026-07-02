// WebSocket transport for the WEB / MOBILE client (A7). Used when the SvelteKit
// SPA is served by the Rust LAN server (axum `lan.rs`) inside a normal browser
// over http(s) — i.e. NOT Tauri (no upstream Channel), NOT dev replay (no live
// stateDir). It connects same-origin to `/ws?loop=<id>&token=<t>`, receives the
// same `Delta` JSON the Tauri Channel emits ({kind:'snapshot'|'state'|'event'}),
// and feeds the store through the existing reduce path (adapters → apply →
// finalize), so the reducer is shared verbatim with desktop.
//
// Resilience (PROTOCOL §7 / plan §6 A7): the socket reconnects on drop with
// exponential backoff; while disconnected a "stale (last seen HH:MM)" badge is
// surfaced via the onStatus callback. On reconnect the server re-sends a fresh
// snapshot, so the client resyncs without replaying from scratch.
//
// Control + answer go UP over HTTP POST to `/api/control` carrying the token;
// when the page has no token the client is observe-only and these no-op (the
// LAN server rejects tokenless control anyway — we disable locally so the UI can
// reflect "observe-only" without a round-trip).

import type { Delta, RawEvent, RunState } from '../types';
import { initialState } from '../reduce';
import { logStore } from '../stores/log.svelte';
import { activityStore } from '../stores/activity.svelte';
import type { Transport, TransportOpts } from './index';

/** Connection lifecycle the UI surfaces as a freshness badge. */
export interface WsStatus {
  state: 'connecting' | 'live' | 'stale' | 'closed';
  lastSeen: number | null; // ms epoch of the last delta received
  attempt: number; // reconnect attempt count (0 while healthy)
  observeOnly: boolean; // no token → control disabled
}

export interface WsConfig {
  loopId: string;
  adapter: string;
  /** Override the same-origin base (tests / explicit host); else location.origin. */
  origin?: string;
  /** Auth token; if omitted we read `?token=` / `#token=` from the page URL. */
  token?: string | null;
  /** Status callback for the freshness badge. */
  onStatus?: (s: WsStatus) => void;
}

const MAX_BACKOFF_MS = 15_000;
const BASE_BACKOFF_MS = 800;
// after this long without a frame we consider the link stale (badge flips)
const STALE_AFTER_MS = 6_000;

/** Read a token from the page URL (?token= or #token=) when not passed in. */
function tokenFromUrl(): string | null {
  if (typeof window === 'undefined') return null;
  const q = new URLSearchParams(window.location.search);
  const fromQuery = q.get('token');
  if (fromQuery) return fromQuery;
  // also tolerate it in the hash (QR deep-links sometimes use #token=)
  const hash = window.location.hash.replace(/^#/, '');
  const h = new URLSearchParams(hash);
  return h.get('token');
}

/** http(s) origin → ws(s) origin. */
function wsOrigin(origin: string): string {
  return origin.replace(/^http/i, 'ws');
}

export class WsTransport implements Transport {
  readonly kind = 'ws' as const;
  private cfg: WsConfig;
  private onState: (s: RunState) => void;
  private state: RunState;
  private ws: WebSocket | null = null;
  private token: string | null;
  private origin: string;
  private backoff = BASE_BACKOFF_MS;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private staleTimer: ReturnType<typeof setTimeout> | null = null;
  private lastSeen: number | null = null;
  private closedByUs = false;
  private status: WsStatus;

  constructor(cfg: WsConfig, opts: TransportOpts) {
    this.cfg = cfg;
    this.onState = opts.onState;
    this.state = initialState(cfg.loopId);
    this.origin =
      cfg.origin ?? (typeof window !== 'undefined' ? window.location.origin : '');
    this.token = cfg.token ?? tokenFromUrl();
    this.status = {
      state: 'connecting',
      lastSeen: null,
      attempt: 0,
      observeOnly: !this.token,
    };
  }

  async start(): Promise<void> {
    this.closedByUs = false;
    this.connect();
  }

  // ── socket lifecycle ───────────────────────────────────────────────────────
  private connect(): void {
    if (typeof WebSocket === 'undefined') return; // SSR / no-ws env: inert
    this.emitStatus('connecting');
    const params = new URLSearchParams({ loop: this.cfg.loopId });
    if (this.token) params.set('token', this.token);
    const url = `${wsOrigin(this.origin)}/ws?${params.toString()}`;

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.backoff = BASE_BACKOFF_MS;
      this.markSeen();
      this.emitStatus('live');
    };
    ws.onmessage = (ev: MessageEvent) => {
      this.markSeen();
      this.emitStatus('live');
      let delta: Delta;
      try {
        delta = JSON.parse(typeof ev.data === 'string' ? ev.data : '') as Delta;
      } catch {
        return; // tolerate a malformed/partial frame
      }
      this.onDelta(delta);
    };
    ws.onclose = () => {
      this.ws = null;
      if (!this.closedByUs) this.scheduleReconnect();
      else this.emitStatus('closed');
    };
    ws.onerror = () => {
      // onclose fires next; let it drive reconnect. Close defensively.
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };
  }

  private scheduleReconnect(): void {
    this.emitStatus('stale');
    this.attempt += 1;
    const jitter = Math.random() * 250;
    const delay = Math.min(MAX_BACKOFF_MS, this.backoff) + jitter;
    this.backoff = Math.min(MAX_BACKOFF_MS, this.backoff * 1.8);
    this.clearReconnect();
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private markSeen(): void {
    this.lastSeen = Date.now();
    // (re)arm the stale watchdog: if no frame arrives for STALE_AFTER_MS while
    // the socket is nominally open, flip the badge to "stale" (server hiccup).
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.staleTimer = setTimeout(() => {
      if (!this.closedByUs) this.emitStatus('stale');
    }, STALE_AFTER_MS);
  }

  private onDelta(delta: Delta): void {
    if (delta.kind === 'event') {
      // raw event → live LOG feed only; the server sends an authoritative `state` delta per batch.
      logStore.push(delta.event as RawEvent);
      return;
    }
    if (delta.kind === 'activity') {
      // liveness heartbeat → the activity store (LIVE LOG freshness dot); never reduced state.
      activityStore.set(delta.activity);
      return;
    }
    // snapshot | state: server already reduced; adopt wholesale (also the reconnect resync).
    this.state = delta.state;
    // fresh reference so the runes store registers the change
    this.onState({ ...this.state });
  }

  stop(): void {
    this.closedByUs = true;
    this.clearReconnect();
    if (this.staleTimer) {
      clearTimeout(this.staleTimer);
      this.staleTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* ignore */
      }
      this.ws = null;
    }
    this.emitStatus('closed');
  }

  // ── control + answer go UP over HTTP (the ws is observe-down only) ──────────
  async control(action: string): Promise<void> {
    if (this.status.observeOnly) {
      // eslint-disable-next-line no-console
      console.info(`[ws] control "${action}" ignored — observe-only (no token).`);
      return;
    }
    await this.post('/api/control', { loopId: this.cfg.loopId, action });
  }

  async answer(qid: string, text: string): Promise<void> {
    if (this.status.observeOnly) {
      // eslint-disable-next-line no-console
      console.info('[ws] answer ignored — observe-only (no token).');
      return;
    }
    await this.post('/api/control', {
      loopId: this.cfg.loopId,
      action: 'answer',
      qid,
      text,
    });
  }

  private async post(path: string, body: Record<string, unknown>): Promise<void> {
    const headers: Record<string, string> = { 'content-type': 'application/json' };
    if (this.token) headers['authorization'] = `Bearer ${this.token}`;
    try {
      const res = await fetch(`${this.origin}${path}`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ ...body, token: this.token ?? undefined }),
      });
      if (!res.ok) {
        // eslint-disable-next-line no-console
        console.warn(`[ws] POST ${path} → ${res.status}`);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn(`[ws] POST ${path} failed`, e);
    }
  }

  // ── status badge ───────────────────────────────────────────────────────────
  private emitStatus(state: WsStatus['state']): void {
    this.status = {
      state,
      lastSeen: this.lastSeen,
      attempt: this.attempt,
      observeOnly: !this.token,
    };
    this.cfg.onStatus?.(this.status);
  }
}

/** A transport that exposes the ws freshness badge. */
export interface WsBadgeTransport extends Transport {
  onStatus(cb: (s: WsStatus) => void): void;
}

export function isWs(t: Transport | null): t is WsTransport {
  return !!t && t instanceof WsTransport;
}
