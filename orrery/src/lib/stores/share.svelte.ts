// Share store (wave U4 Task 1) — "share to phone". Wraps the Rust LAN server commands
// (`start_lan_server(loopsDir, port?) -> {url, token}` / `stop_lan_server()`, PROTOCOL.md
// §A7 / src-tauri/src/lan.rs) so the Cosmos can offer a QR + link a phone on the same
// Wi-Fi can open to watch (and, with the token, drive) a loop.
//
// Follows the same hasTauri()-gated dev-fallback convention as cosmosStore.createLoop /
// probeCommand: in Tauri it invokes the real commands; in dev preview (`vite dev`, no
// Tauri bridge) it synthesizes an obviously-fake LanInfo (a documentation-only IP,
// RFC 5737 TEST-NET-1) so the popover + QR can still be exercised/screenshotted without a
// real server — `simulated` flags this so the UI can say so instead of implying a phone
// could actually reach it.

import { browser } from '$app/environment';
import { hasTauri } from '../transport';
import { DEFAULT_LOOPS_DIR } from '../paths';

export interface LanInfo {
  url: string;
  token: string;
}

type ShareStatus = 'idle' | 'starting' | 'active' | 'error';

// A fixed, obviously-non-routable token/url pair for the dev-preview fallback — never a
// real reachable server. 192.0.2.0/24 is reserved for documentation (RFC 5737).
const DEV_FAKE_INFO: LanInfo = {
  url: 'http://192.0.2.10:8787',
  token: '0123456789abcdef0123456789abcdef',
};

class ShareStore {
  /** Popover visibility — independent of whether the server is actually running (closing
   * the popover does NOT stop sharing; only the explicit "stop sharing" button does). */
  open = $state(false);
  status = $state<ShareStatus>('idle');
  info = $state<LanInfo | null>(null);
  error = $state<string | null>(null);
  /** true when `info` is the dev-preview stand-in, not a real running server. */
  simulated = $state(false);

  /**
   * The exact URL a phone browser needs to mount the WsTransport instead of falling back
   * to dev replay — see transport/index.ts `hasWsServer()` (accepts `?token=` alone, or
   * `?ws=1`, in either the query or the hash) and transport/ws.ts `tokenFromUrl()` (reads
   * `?token=` / `#token=`). We carry both `token` and `ws=1` so the signal is explicit even
   * if a future change tightens hasWsServer()'s heuristic.
   */
  get shareUrl(): string | null {
    if (!this.info) return null;
    return `${this.info.url}/?token=${this.info.token}&ws=1`;
  }

  /** Open the popover; starts the server the first time (idempotent — reopening while
   * already active just shows the existing url/token, it never restarts the server). */
  async openPopover(): Promise<void> {
    this.open = true;
    if (this.status === 'idle' || this.status === 'error') {
      await this.start();
    }
  }

  closePopover(): void {
    this.open = false;
  }

  async start(): Promise<void> {
    if (!browser || this.status === 'starting') return;
    this.status = 'starting';
    this.error = null;
    try {
      if (hasTauri()) {
        const { invoke } = await import('@tauri-apps/api/core');
        const info = (await invoke('start_lan_server', {
          loopsDir: DEFAULT_LOOPS_DIR,
        })) as LanInfo;
        this.info = info;
        this.simulated = false;
      } else {
        // dev preview (no Tauri bridge) — mirrors cosmosStore.probeCommand's SIMULATED
        // fallback: render the real UI with an obviously-fake token instead of pretending
        // to talk to a backend that isn't there.
        await new Promise((r) => setTimeout(r, 250));
        this.info = DEV_FAKE_INFO;
        this.simulated = true;
      }
      this.status = 'active';
    } catch (e) {
      this.status = 'error';
      this.error = e instanceof Error ? e.message : String(e);
    }
  }

  async stop(): Promise<void> {
    if (!browser) return;
    try {
      if (hasTauri()) {
        const { invoke } = await import('@tauri-apps/api/core');
        await invoke('stop_lan_server');
      }
    } catch (e) {
      // best-effort — still clear local state so the UI doesn't claim to be sharing
      // something the backend may have already torn down.
      this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.info = null;
      this.status = 'idle';
      this.simulated = false;
      this.open = false;
    }
  }
}

export const shareStore = new ShareStore();
export type { ShareStore };
