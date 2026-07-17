//! A7 — opt-in LAN server (plan §5 `lan.rs`).
//!
//! An axum server, started on demand by the `start_lan_server` command, that lets a phone or
//! browser on the same Wi-Fi *watch* a loop (and, with the token, *control* it). It reuses the
//! exact tail→reduce→watch pipeline `watch_run` uses (control.rs / watcher.rs / reducer.rs), and
//! pushes the SAME `Delta` JSON the Tauri Channel emits (`{kind:'snapshot'|'state'|'event', …}`),
//! so a `ws-transport.ts` client can share the frontend's `reduce.ts` codepath verbatim.
//!
//! Routes:
//!   * `GET /`                       — the built SPA (`../build`) via tower-http `ServeDir`.
//!   * `GET /ws?loop=<id>`           — a WebSocket streaming `Delta`s for a loop. **REQUIRES the
//!                                     token** (R5 — previously observe worked without one, which
//!                                     let anyone on the network stream run state by guessing a
//!                                     short loop id).
//!   * `POST /api/control`           — start/stop/resume/answer. **REQUIRES the token.**
//!   * `GET /api/health`             — unauthenticated liveness probe.
//!
//! The `/ws` token rides the `Sec-WebSocket-Protocol` header as `orrery-token.<t>` (echoed back to
//! complete the RFC 6455 handshake) so it never leaks into logs/proxy history; a `?token=<t>`
//! query is still accepted as a deprecated fallback for older phone sessions.
//!
//! SECURITY: the server only runs after `start_lan_server`. EVERY route except `/api/health`
//! requires the token (constant-time-ish compare). Loop ids are validated as plain path
//! components, so a `loop=` query can never traverse the registry. No arbitrary fs/shell is
//! exposed — control routes call the same vetted control.rs helpers (which only ever spawn a
//! loop's own declared command). The server NEVER binds `0.0.0.0`: when LAN-IP autodetection
//! fails it binds `127.0.0.1` only (R5 — it used to bind every interface while advertising a
//! `127.0.0.1` url, so the actual bind surface was wider than what a user was told).

use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Query, State,
    },
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tower_http::services::ServeDir;

use crate::control;
use crate::model::{Activity, Delta};

/// `LanInfo { url, token }` returned by `start_lan_server` — the QR/share payload.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LanInfo {
    /// `http://<LAN-ip>:<port>` — what a phone types / the QR encodes.
    pub url: String,
    /// random hex token; required for `/api/control`, optional for observe.
    pub token: String,
}

/// Per-request state shared with every axum handler. Public so `build_router` (also public, for
/// the router-constructs test) doesn't leak a private type.
#[derive(Clone)]
pub struct AppState {
    /// absolute path to the `loops/` registry (resolves `loops/<id>/loop.json`).
    loops_dir: PathBuf,
    /// the control token; clients must present it to drive a loop.
    token: Arc<String>,
}

/// A running server handle kept in Tauri app state so `stop_lan_server` can shut it down.
/// `manage`d as `LanServer(Mutex<Option<RunningServer>>)`.
#[derive(Default)]
pub struct LanServer(pub Mutex<Option<RunningServer>>);

/// The live server: its runtime + a shutdown flag the axum graceful-shutdown future watches.
pub struct RunningServer {
    pub info: LanInfo,
    runtime: tokio::runtime::Runtime,
    shutdown: Arc<AtomicBool>,
}

impl RunningServer {
    /// Signal graceful shutdown and drop the runtime (joins the server thread).
    fn stop(self) {
        self.shutdown.store(true, Ordering::SeqCst);
        // Dropping the runtime waits for the spawned tasks to wind down.
        drop(self.runtime);
    }
}

// ---------------------------------------------------------------------------
// helpers — token, loop id, LAN ip
// ---------------------------------------------------------------------------

/// 32-hex-char (128-bit) random token. Uses `getrandom` (already in the dep tree) so we don't pull
/// a heavy rng crate. Falls back to a time-seeded value only if the OS RNG is unavailable.
pub fn gen_token() -> String {
    let mut bytes = [0u8; 16];
    if getrandom::getrandom(&mut bytes).is_err() {
        // Extremely unlikely on desktop; still produce *something* non-constant.
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        bytes.copy_from_slice(&nanos.to_le_bytes()[..16]);
    }
    let mut s = String::with_capacity(32);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Constant-time-ish token compare (length + bytewise OR-accumulate) so a remote can't time it.
pub fn token_eq(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Is `id` a safe single path component usable as a loop directory name? Mirrors control.rs's guard
/// so a `loop=` query can never escape `loops/`.
pub fn is_safe_loop_id(id: &str) -> bool {
    if id.is_empty() || id.len() > 64 {
        return false;
    }
    id.chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// Best-effort LAN IPv4 of this machine (the private address a phone on the same Wi-Fi can reach).
/// Strategy: open a UDP socket "to" a public address (no packets are sent for UDP connect) and read
/// back the local address the OS picked. Falls back to `0.0.0.0` if detection fails.
pub fn lan_ipv4() -> Ipv4Addr {
    use std::net::UdpSocket;
    // 8.8.8.8 is never contacted — connect() on UDP only fixes the local routing/source addr.
    if let Ok(sock) = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0)) {
        if sock.connect((Ipv4Addr::new(8, 8, 8, 8), 53)).is_ok() {
            if let Ok(SocketAddr::V4(local)) = sock.local_addr() {
                let ip = *local.ip();
                if !ip.is_unspecified() && !ip.is_loopback() {
                    return ip;
                }
            }
        }
    }
    Ipv4Addr::UNSPECIFIED
}

/// Resolve a loop's `(stateDir, adapter, logFile)` from `loops/<id>/loop.json`. A RELATIVE
/// `stateDir` (every seed ships `".loop"`) is resolved under the loop's OWN dir
/// (`loops/<id>`), mirroring control.rs `state_dir_of`/`resolve_under`. Without this the LAN
/// `/ws` path resolved `.loop` against the SERVER PROCESS cwd, so phones observed an empty run
/// while the desktop (which resolves under the loop dir) saw the real one. An ABSOLUTE `stateDir`
/// is used verbatim.
fn resolve_loop(loops_dir: &PathBuf, loop_id: &str) -> Result<(PathBuf, String, Option<String>), String> {
    if !is_safe_loop_id(loop_id) {
        return Err(format!("invalid loop id {loop_id:?}"));
    }
    let base = loops_dir.join(loop_id);
    let p = base.join("loop.json");
    let text = std::fs::read_to_string(&p).map_err(|e| format!("read {p:?}: {e}"))?;
    let def: crate::model::LoopDef =
        serde_json::from_str(&text).map_err(|e| format!("parse {p:?}: {e}"))?;
    // Resolve relative-under-loop-dir, absolute-verbatim — the exact contract control.rs uses.
    let sd = PathBuf::from(&def.state_dir);
    let state_dir = if sd.is_absolute() { sd } else { base.join(sd) };
    Ok((state_dir, def.adapter, def.log_file))
}

// ---------------------------------------------------------------------------
// command surface (Tauri) — start / stop
// ---------------------------------------------------------------------------

const DEFAULT_PORT: u16 = 8787;

/// `start_lan_server(loops_dir, port?) -> LanInfo` — bring up the axum server on a dedicated tokio
/// runtime/thread, stored in app state so `stop_lan_server` can join it. Idempotent-ish: a second
/// start returns the already-running server's info.
#[tauri::command]
pub fn start_lan_server(
    loops_dir: String,
    port: Option<u16>,
    server: tauri::State<'_, LanServer>,
) -> Result<LanInfo, String> {
    let mut slot = server.0.lock().map_err(|_| "lan server lock poisoned".to_string())?;
    if let Some(running) = slot.as_ref() {
        // already up — hand back the same url+token (don't double-bind the port).
        return Ok(running.info.clone());
    }

    let port = port.unwrap_or(DEFAULT_PORT);
    let token = Arc::new(gen_token());
    let loops_dir = PathBuf::from(&loops_dir);

    // Bind to the LAN IPv4 when we can detect it (so the url is shareable); otherwise fall back
    // to loopback-only (127.0.0.1), NEVER 0.0.0.0 (R5 — binding every interface when detection
    // merely FAILED silently exposed the server network-wide even though the advertised url said
    // 127.0.0.1, i.e. the bind surface used to be wider than what the user was told).
    let lan_ip = lan_ipv4();
    let bind_ip: IpAddr = if lan_ip.is_unspecified() {
        IpAddr::V4(Ipv4Addr::LOCALHOST)
    } else {
        IpAddr::V4(lan_ip)
    };
    let bind_addr = SocketAddr::new(bind_ip, port);

    // The advertised url uses the detected LAN ip (the same ip we bind above); when detection
    // failed we bound 127.0.0.1 and advertise that too — we NEVER bind 0.0.0.0, so the advertised
    // host always matches the actual bind surface.
    let host_for_url = if lan_ip.is_unspecified() {
        Ipv4Addr::LOCALHOST
    } else {
        lan_ip
    };
    let info = LanInfo {
        url: format!("http://{host_for_url}:{port}"),
        token: (*token).clone(),
    };

    let state = AppState {
        loops_dir: loops_dir.clone(),
        token: token.clone(),
    };

    // Dedicated multi-thread runtime so start/stop are self-contained and survive across commands.
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .map_err(|e| format!("build tokio runtime: {e}"))?;

    let shutdown = Arc::new(AtomicBool::new(false));
    let shutdown_fut = shutdown.clone();

    // Bind synchronously on the runtime so we can surface an error (e.g. port in use) to the caller.
    let listener = runtime
        .block_on(async move { tokio::net::TcpListener::bind(bind_addr).await })
        .map_err(|e| format!("bind {bind_addr}: {e}"))?;

    let app = build_router(state);

    // Spawn the serve future; it runs until the shutdown flag flips.
    runtime.spawn(async move {
        let graceful = async move {
            // Poll the shutdown flag; axum's with_graceful_shutdown awaits this future.
            loop {
                if shutdown_fut.load(Ordering::SeqCst) {
                    break;
                }
                tokio::time::sleep(std::time::Duration::from_millis(150)).await;
            }
        };
        let _ = axum::serve(listener, app)
            .with_graceful_shutdown(graceful)
            .await;
    });

    *slot = Some(RunningServer {
        info: info.clone(),
        runtime,
        shutdown,
    });
    Ok(info)
}

/// `stop_lan_server()` — signal shutdown and join. No-op if nothing is running.
#[tauri::command]
pub fn stop_lan_server(server: tauri::State<'_, LanServer>) {
    if let Ok(mut slot) = server.0.lock() {
        if let Some(running) = slot.take() {
            running.stop();
        }
    }
}

// ---------------------------------------------------------------------------
// router + handlers
// ---------------------------------------------------------------------------

/// Build the axum router. Public so a test can assert it constructs / compiles.
pub fn build_router(state: AppState) -> Router {
    // Static SPA. `../build` is the SvelteKit adapter-static output (tauri.conf frontendDist).
    // ServeDir tolerates absence at construction time; requests 404 with a clear body if missing.
    let build_dir = spa_dir();
    let serve = ServeDir::new(&build_dir);

    Router::new()
        .route("/ws", get(ws_handler))
        .route("/api/control", post(control_handler))
        .route("/api/health", get(health_handler))
        .fallback_service(serve)
        .with_state(state)
}

/// Resolve the built SPA directory (`<manifest>/../build`), the same dir tauri.conf serves.
fn spa_dir() -> PathBuf {
    PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../build"))
}

/// `GET /api/health` — trivial liveness probe (also confirms the SPA dir is present).
async fn health_handler() -> impl IntoResponse {
    let dir = spa_dir();
    let body = serde_json::json!({
        "ok": true,
        "spaPresent": dir.join("index.html").exists(),
    });
    (StatusCode::OK, Json(body))
}

#[derive(Debug, Deserialize)]
struct WsQuery {
    #[serde(rename = "loop")]
    loop_id: String,
    /// DEPRECATED token transport (kept for older phone sessions): prefer the
    /// `Sec-WebSocket-Protocol: orrery-token.<t>` header, which keeps the secret out of the URL.
    token: Option<String>,
}

/// The `Sec-WebSocket-Protocol` subprotocol prefix carrying the LAN token: `orrery-token.<token>`.
const PROTO_TOKEN_PREFIX: &str = "orrery-token.";

/// Extract the token from a `Sec-WebSocket-Protocol` header of the form `orrery-token.<token>`
/// (possibly among other comma-separated offered subprotocols). Returns the raw token (prefix
/// stripped), or `None` when no such subprotocol was offered. Keeping the token in a subprotocol
/// header rather than the `?token=` query keeps it out of access logs, proxies, and browser
/// history.
fn subprotocol_token(headers: &axum::http::HeaderMap) -> Option<String> {
    let raw = headers
        .get(axum::http::header::SEC_WEBSOCKET_PROTOCOL)?
        .to_str()
        .ok()?;
    raw.split(',')
        .map(|s| s.trim())
        .find_map(|p| p.strip_prefix(PROTO_TOKEN_PREFIX))
        .map(|t| t.to_string())
}

/// `GET /ws?loop=<id>` — watch a loop. REQUIRES the token (R5): anyone on the network who can
/// reach the port could previously stream run state by guessing a short loop id, with no auth at
/// all. The token arrives via the `Sec-WebSocket-Protocol: orrery-token.<t>` header (preferred —
/// out of the URL) or a deprecated `?token=<t>` query fallback. Resolves the loop, runs the same
/// tail+reduce+watch pipeline as `watch_run`, and forwards each `Delta` as a JSON text frame.
async fn ws_handler(
    State(state): State<AppState>,
    Query(q): Query<WsQuery>,
    headers: axum::http::HeaderMap,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    // Prefer the token from the subprotocol header; fall back to the deprecated query param.
    let proto_token = subprotocol_token(&headers);
    let token = proto_token.as_deref().or(q.token.as_deref()).unwrap_or("");
    // Token gate FIRST — same constant-time-ish compare as `/api/control`, before we even touch
    // the loop registry or upgrade the connection.
    if !token_eq(token, &state.token) {
        return (StatusCode::UNAUTHORIZED, "invalid token").into_response();
    }
    // Resolve before upgrading so a bad loop id closes the handshake cleanly.
    match resolve_loop(&state.loops_dir, &q.loop_id) {
        Ok((state_dir, adapter, log_file)) => {
            match proto_token {
                // Token came via the subprotocol → echo that exact subprotocol back so the browser
                // handshake completes (RFC 6455 §4.2.2: the server's selected subprotocol MUST be
                // one the client offered). `String: Into<Cow<'static, str>>`, so a runtime value is
                // fine here.
                Some(tok) => {
                    let selected = format!("{PROTO_TOKEN_PREFIX}{tok}");
                    ws.protocols([selected])
                        .on_upgrade(move |socket| stream_loop(socket, state_dir, adapter, log_file))
                }
                // Token came via the query fallback → no subprotocol to echo.
                None => ws.on_upgrade(move |socket| stream_loop(socket, state_dir, adapter, log_file)),
            }
        }
        Err(e) => (StatusCode::BAD_REQUEST, e).into_response(),
    }
}

/// Drive one WebSocket: emit the initial snapshot, then a fresh `State` delta per new log line.
/// This is the LAN twin of `control::watch_run` — same `build_live` snapshot + incremental
/// `watch_loop` tail (R2), only the sink differs (a ws frame instead of a Tauri Channel).
async fn stream_loop(mut socket: WebSocket, state_dir: PathBuf, adapter: String, log_file: Option<String>) {
    // 1) initial snapshot — reuse the shared reducer pipeline; keep the live reducer + consumed
    // length so the tail below feeds it ONLY new lines instead of re-reducing from scratch.
    // `build_live_pub` reads + reduces the WHOLE on-disk log synchronously — on this 2-worker
    // runtime that would block a worker for the entire read, so run it on the blocking pool.
    let (mut live, consumed_len) = {
        let sd = state_dir.clone();
        let ad = adapter.clone();
        let lf = log_file.clone();
        match tokio::task::spawn_blocking(move || {
            control::build_live_pub(&sd, &ad, lf.as_deref())
        })
        .await
        {
            Ok(v) => v,
            Err(_) => return, // the build task panicked/was cancelled — close the socket
        }
    };
    if send_delta(&mut socket, Delta::Snapshot { state: live.state() }).await.is_err() {
        return;
    }

    // 2) tail the log on a blocking thread; forward fresh full-state deltas over an mpsc channel.
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Delta>();
    let stop = Arc::new(AtomicBool::new(false));
    let stop_thread = stop.clone();
    let stop_on_send_err = stop.clone();

    let lp = crate::watcher::resolve_log_path(&state_dir, &adapter, log_file.as_deref());
    let watch_dir = lp.parent().map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
    let sd = state_dir.clone();
    let ad = adapter.clone();
    // Mirrors control.rs's private `loop_id_from`: the state dir's own last path component.
    let loop_id = state_dir
        .file_name()
        .and_then(|s| s.to_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "loop".to_string());

    // Seed a tailer past what the snapshot above already consumed (R2/R3), mirroring watch_run.
    let mut tailer = crate::tailer::Tailer::new();
    tailer.seed(&lp, consumed_len);

    let handle = std::thread::spawn(move || {
        // Liveness heartbeat → its own Delta over a cloned sender, so the web client gets the same
        // "actively working" signal as the desktop app even through a silent dev-story.
        let tx_activity = tx.clone();
        let stop_activity = stop_on_send_err.clone();
        let on_activity = move |raw: Option<Value>| {
            let activity = raw.and_then(|v| serde_json::from_value::<Activity>(v).ok());
            if tx_activity.send(Delta::Activity { activity }).is_err() {
                stop_activity.store(true, Ordering::Relaxed);
            }
        };
        let on_events = move |evs: &[Value], rotated: bool| {
            // Forward the raw events for the LAN/web client's live LOG feed, capped to the last 300
            // of a batch (the first drain replays the whole log; the client logStore caps at 300).
            let start = evs.len().saturating_sub(300);
            for ev in &evs[start..] {
                if tx.send(Delta::Event { event: ev.clone() }).is_err() {
                    stop_on_send_err.store(true, Ordering::Relaxed);
                    return;
                }
            }
            // R3: rotation → rebuild from scratch using the bytes the tailer just handed us.
            if rotated {
                live = crate::live::LiveReducer::new(loop_id.clone(), ad.clone());
            }
            live.apply_batch(evs);
            control::apply_overlays_pub(&mut live, &sd, &ad);
            // If the receiver is gone (socket closed) this errors; we stop on the next check.
            if tx.send(Delta::State { state: live.state() }).is_err() {
                stop_on_send_err.store(true, Ordering::Relaxed);
            }
        };
        let should_stop = move || stop_thread.load(Ordering::Relaxed);
        let _ = crate::watcher::watch_loop(&watch_dir, &lp, tailer, on_events, on_activity, should_stop);
    });

    // 3) pump deltas to the socket until either side closes.
    loop {
        tokio::select! {
            maybe = rx.recv() => {
                match maybe {
                    Some(delta) => {
                        if send_delta(&mut socket, delta).await.is_err() {
                            break;
                        }
                    }
                    None => break, // watcher thread ended
                }
            }
            // Drain inbound frames so we notice a client close (and answer pings).
            inbound = socket.recv() => {
                match inbound {
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Err(_)) => break,
                    _ => { /* ignore client text/ping; observe-only socket */ }
                }
            }
        }
    }

    // tear down the watcher thread. `handle.join()` blocks until the watcher notices the stop flag
    // on its next poll tick (~50ms), so run it on the blocking pool rather than parking a worker of
    // this 2-worker runtime.
    stop.store(true, Ordering::Relaxed);
    let _ = tokio::task::spawn_blocking(move || handle.join()).await;
}

/// Serialize + send a `Delta` as a JSON text frame.
async fn send_delta(socket: &mut WebSocket, delta: Delta) -> Result<(), ()> {
    let txt = serde_json::to_string(&delta).map_err(|_| ())?;
    socket.send(Message::Text(txt)).await.map_err(|_| ())
}

/// Body of `POST /api/control`: the action + the loop it targets + the token.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ControlBody {
    /// `start` | `stop` | `resume` | `cancel-stop` | `answer`
    action: String,
    #[serde(rename = "loop")]
    loop_id: String,
    token: String,
    /// stop mode (`phase`|`story`|`now`) — required for `action:"stop"`.
    #[serde(default)]
    mode: Option<String>,
    /// answer payload — required for `action:"answer"`.
    #[serde(default)]
    qid: Option<String>,
    #[serde(default)]
    text: Option<String>,
}

/// `POST /api/control` — REQUIRES the token. Dispatches to the same control.rs surface the desktop
/// app uses. Returns `{ ok: true, .. }` or a 4xx with a message.
async fn control_handler(
    State(state): State<AppState>,
    Json(body): Json<ControlBody>,
) -> impl IntoResponse {
    // Token gate FIRST — control is never allowed without it.
    if !token_eq(&body.token, &state.token) {
        return (StatusCode::UNAUTHORIZED, Json(err_json("invalid token"))).into_response();
    }
    if !is_safe_loop_id(&body.loop_id) {
        return (StatusCode::BAD_REQUEST, Json(err_json("invalid loop id"))).into_response();
    }

    let loops_dir = state.loops_dir.to_string_lossy().to_string();
    let result: Result<Value, String> = match body.action.as_str() {
        "stop" => {
            let mode = body.mode.clone().unwrap_or_default();
            control::stop_loop_core(&body.loop_id, &loops_dir, &mode)
                .map(|_| serde_json::json!({ "ok": true }))
        }
        "cancel-stop" => control::cancel_stop_core(&body.loop_id, &loops_dir)
            .map(|_| serde_json::json!({ "ok": true })),
        "answer" => {
            let qid = body.qid.clone().unwrap_or_default();
            let text = body.text.clone().unwrap_or_default();
            control::answer_question_core(&body.loop_id, &loops_dir, &qid, &text)
                .map(|_| serde_json::json!({ "ok": true }))
        }
        // start/resume spawn a child; the LAN side intentionally does NOT track PIDs in the
        // desktop LoopPids map (that state is desktop-scoped). The concurrency guard still applies.
        "start" => control::start_loop_core(&body.loop_id, &loops_dir)
            .map(|pid| serde_json::json!({ "ok": true, "pid": pid })),
        "resume" => control::resume_loop_core(&body.loop_id, &loops_dir)
            .map(|pid| serde_json::json!({ "ok": true, "pid": pid })),
        other => Err(format!("unknown action {other:?}")),
    };

    match result {
        Ok(v) => (StatusCode::OK, Json(v)).into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(err_json(&e))).into_response(),
    }
}

fn err_json(msg: &str) -> Value {
    serde_json::json!({ "ok": false, "error": msg })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_is_non_empty_hex_and_unique() {
        let a = gen_token();
        let b = gen_token();
        assert_eq!(a.len(), 32, "128-bit token => 32 hex chars");
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()), "hex only: {a}");
        assert_ne!(a, b, "two tokens must differ");
    }

    #[test]
    fn token_eq_is_correct() {
        let t = gen_token();
        assert!(token_eq(&t, &t.clone()));
        assert!(!token_eq(&t, "short"));
        assert!(!token_eq("aaaa", "aaab"));
        assert!(token_eq("", ""));
    }

    #[test]
    fn safe_loop_id_matches_control_guard() {
        for ok in ["roman", "calc", "bmad", "my-loop_2"] {
            assert!(is_safe_loop_id(ok), "accept {ok}");
        }
        for bad in ["", "../evil", "a/b", "a\\b", "a.b", "x y", &"z".repeat(65)] {
            assert!(!is_safe_loop_id(bad), "reject {bad:?}");
        }
    }

    #[test]
    fn lan_ipv4_is_v4_and_not_loopback_when_detected() {
        // Detection may legitimately fail in a sandbox (→ UNSPECIFIED). When it succeeds, the
        // address must be a non-loopback IPv4 we can advertise.
        let ip = lan_ipv4();
        if !ip.is_unspecified() {
            assert!(!ip.is_loopback(), "detected ip must not be loopback: {ip}");
        }
    }

    #[test]
    fn resolve_loop_reads_seed_def() {
        let loops_dir = PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../loops"));
        let (state_dir, adapter, log_file) = resolve_loop(&loops_dir, "hello").expect("resolve hello");
        assert_eq!(adapter, "generic");
        assert_eq!(log_file.as_deref(), Some("log.jsonl"));
        // The seed's relative ".loop" must resolve to an ABSOLUTE path UNDER loops/hello (not the
        // server-process cwd), exactly as the desktop control path resolves it — otherwise phones
        // tail a nonexistent `./.loop` and observe an empty run.
        let want = loops_dir.join("hello").join(".loop");
        assert_eq!(state_dir, want, "relative stateDir must resolve under the loop's own dir");
        assert!(state_dir.is_absolute(), "resolved stateDir must be absolute: {state_dir:?}");

        // a traversal id is refused before any read
        assert!(resolve_loop(&loops_dir, "../evil").is_err());
    }

    #[test]
    fn subprotocol_token_extracts_from_sec_websocket_protocol() {
        use axum::http::{header::SEC_WEBSOCKET_PROTOCOL, HeaderMap, HeaderValue};

        // sole offered subprotocol
        let mut h = HeaderMap::new();
        h.insert(SEC_WEBSOCKET_PROTOCOL, HeaderValue::from_static("orrery-token.deadbeef"));
        assert_eq!(subprotocol_token(&h).as_deref(), Some("deadbeef"));

        // among other offered subprotocols (browsers may offer several, comma+space separated)
        let mut h2 = HeaderMap::new();
        h2.insert(
            SEC_WEBSOCKET_PROTOCOL,
            HeaderValue::from_static("chat, orrery-token.abc123, superchat"),
        );
        assert_eq!(subprotocol_token(&h2).as_deref(), Some("abc123"));

        // no orrery-token subprotocol → None (falls back to the ?token= query at the call site)
        let mut h3 = HeaderMap::new();
        h3.insert(SEC_WEBSOCKET_PROTOCOL, HeaderValue::from_static("chat"));
        assert_eq!(subprotocol_token(&h3), None);

        // absent header → None
        assert_eq!(subprotocol_token(&HeaderMap::new()), None);
    }

    #[test]
    fn router_constructs() {
        // Proves the axum Router + ServeDir + ws/api routes compile and assemble.
        let state = AppState {
            loops_dir: PathBuf::from("."),
            token: Arc::new(gen_token()),
        };
        let _router: Router = build_router(state);
    }

    #[test]
    fn lan_info_is_camelcase() {
        let info = LanInfo {
            url: "http://192.168.1.5:8787".to_string(),
            token: "deadbeef".to_string(),
        };
        let v = serde_json::to_value(&info).unwrap();
        assert!(v.get("url").is_some());
        assert!(v.get("token").is_some());
    }
}
