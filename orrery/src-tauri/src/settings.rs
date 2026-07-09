//! WS-D — settings + multi-provider BYOK backend (contract: `src/lib/settings/contract.ts`).
//!
//! This module owns:
//!   * the on-disk `settings.json` path (resolved from an app-config-dir `OnceLock` so the
//!     path — and `byok_env()` — work even from the LAN control routes, which have no
//!     `AppHandle`),
//!   * the OS-keychain seam for BYOK secrets (`keychain_*`, via `keyring`), which NEVER
//!     returns a secret to the webview and NEVER logs one,
//!   * a Rust MIRROR of `PROVIDER_MATRIX` (schema.ts) and `byok_env()` — the spawn-time
//!     env inject/scrub the engine child is launched with (applied in `control::spawn_detached`),
//!   * export/import/watch of `settings.json`.
//!
//! The command NAMES here MUST equal the `CMD` values in `contract.ts`.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, SystemTime};

use notify::RecursiveMode;
use notify_debouncer_mini::{new_debouncer, DebounceEventResult};
use serde::Serialize;
use serde_json::Value;

/// The OS-keychain SERVICE name every BYOK secret is stored under (per-account = the matrix
/// `keychainAccount`). Fixed by the contract; do not change without a migration.
const KEYRING_SERVICE: &str = "dev.loop.orrery";

/// The persisted settings filename under the app config dir (mirrors `STORE_FILE` in contract.ts).
const STORE_FILE: &str = "settings.json";

// ---------------------------------------------------------------------------
// Config-dir resolution — set ONCE from the AppHandle in lib.rs `setup()`, then
// readable WITHOUT a handle (the LAN control path has none). This is what lets
// `byok_env()` resolve settings.json from `start_loop_core`/`resume_loop_core`.
// ---------------------------------------------------------------------------

static CONFIG_DIR: OnceLock<PathBuf> = OnceLock::new();

/// Record the app config dir (where tauri-plugin-store writes `settings.json`). Called by
/// `lib.rs` setup(). Idempotent — a second call is ignored (OnceLock semantics).
pub fn init_config_dir(dir: PathBuf) {
    let _ = CONFIG_DIR.set(dir);
}

/// The app config dir, if `init_config_dir` has run.
fn config_dir() -> Option<PathBuf> {
    CONFIG_DIR.get().cloned()
}

/// Absolute path of `settings.json`, if the config dir is known.
fn settings_path() -> Option<PathBuf> {
    config_dir().map(|d| d.join(STORE_FILE))
}

/// Read + parse `settings.json`. `None` when the config dir is unknown, the file is absent, or
/// the JSON is malformed — every caller treats that as "no settings" (fall back to defaults).
fn read_settings_value() -> Option<Value> {
    let path = settings_path()?;
    let text = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str::<Value>(&text).ok()
}

// ---------------------------------------------------------------------------
// settings_config_path / open_settings_file / resolve_loops_dir
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn settings_config_path() -> String {
    settings_path()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default()
}

#[tauri::command]
pub fn open_settings_file(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let path = settings_path().ok_or_else(|| "settings path not initialized".to_string())?;
    // Ensure the file exists so the editor has something to open on a fresh install (the store
    // only creates it on first save). An empty object is a valid settings.json the store merges.
    if !path.exists() {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(&path, "{}\n");
    }
    app.opener()
        .open_path(path.to_string_lossy().into_owned(), None::<&str>)
        .map_err(|e| e.to_string())
}

/// The built-in default loops dir, MIRRORING vite.config.js: the `ORRERY_LOOPS_DIR` build env
/// when set, else `<repo>/orrery/loops` of the checkout being built (CARGO_MANIFEST_DIR is
/// `<repo>/orrery/src-tauri`). Kept identical to the frontend `DEFAULT_LOOPS_DIR` so a missing
/// override resolves to the same place on both sides.
fn builtin_default_loops_dir() -> String {
    if let Some(v) = option_env!("ORRERY_LOOPS_DIR") {
        if !v.is_empty() {
            return v.to_string();
        }
    }
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(|p| p.join("loops"))
        .unwrap_or_else(|| PathBuf::from("loops"))
        .to_string_lossy()
        .into_owned()
}

/// Precedence-only core (testable without the config-dir OnceLock): `general.loopsDir` when set +
/// non-empty, else the built-in default.
fn resolve_loops_dir_from(settings: Option<&Value>) -> String {
    settings
        .and_then(|v| v.get("general"))
        .and_then(|g| g.get("loopsDir"))
        .and_then(|d| d.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(builtin_default_loops_dir)
}

#[tauri::command]
pub fn resolve_loops_dir() -> String {
    resolve_loops_dir_from(read_settings_value().as_ref())
}

// ---------------------------------------------------------------------------
// Keychain (OS keyring). The secret is only ever read Rust-side; it is NEVER
// returned to the webview and NEVER logged.
// ---------------------------------------------------------------------------

/// Internal secret read — the ONLY place a secret leaves the keychain, used by `byok_env()` and
/// (as a presence check) `keychain_has` / `byok_auth_status`. Returns `None` on any keyring error.
fn keychain_get(account: &str) -> Option<String> {
    keyring::Entry::new(KEYRING_SERVICE, account)
        .ok()?
        .get_password()
        .ok()
}

#[tauri::command]
pub fn keychain_set(account: String, secret: String) -> Result<(), String> {
    let entry = keyring::Entry::new(KEYRING_SERVICE, &account).map_err(|e| e.to_string())?;
    // NOTE: never log `secret`.
    entry.set_password(&secret).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn keychain_has(account: String) -> bool {
    keychain_get(&account).is_some()
}

#[tauri::command]
pub fn keychain_delete(account: String) -> Result<(), String> {
    let entry = keyring::Entry::new(KEYRING_SERVICE, &account).map_err(|e| e.to_string())?;
    match entry.delete_credential() {
        Ok(()) => Ok(()),
        // Idempotent: deleting an absent secret is success, not an error.
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

// ---------------------------------------------------------------------------
// PROVIDER_MATRIX — a Rust MIRROR of schema.ts's PROVIDER_MATRIX. Keep this the
// single auditable source on the Rust side; every supported (provider, runner,
// mode) triple appears exactly once. Anything absent is UNSUPPORTED (inherit env).
// ---------------------------------------------------------------------------

struct MatrixEntry {
    provider: &'static str,
    runner: &'static str,
    mode: &'static str,
    /// env vars the backend sets at spawn (values sourced from the instance / keychain).
    inject: &'static [&'static str],
    /// env vars the backend removes from the inherited environment.
    scrub: &'static [&'static str],
    /// OS-keychain account the secret lives under, or `None` when the mode needs no secret.
    keychain_account: Option<&'static str>,
}

/// Mirrors `PROVIDER_MATRIX` (schema.ts) verbatim. All entries are supported; absence = unsupported.
const PROVIDER_MATRIX: &[MatrixEntry] = &[
    MatrixEntry {
        provider: "anthropic",
        runner: "claude",
        mode: "subscription",
        inject: &[],
        scrub: &["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"],
        keychain_account: None,
    },
    MatrixEntry {
        provider: "anthropic",
        runner: "claude",
        mode: "apiKey",
        inject: &["ANTHROPIC_API_KEY"],
        scrub: &["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"],
        keychain_account: Some("orrery/anthropic/api-key"),
    },
    MatrixEntry {
        provider: "anthropic",
        runner: "aider",
        mode: "apiKey",
        inject: &["ANTHROPIC_API_KEY"],
        scrub: &[],
        keychain_account: Some("orrery/anthropic/api-key"),
    },
    MatrixEntry {
        provider: "openai",
        runner: "codex",
        mode: "subscription",
        inject: &[],
        scrub: &["OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"],
        keychain_account: None,
    },
    MatrixEntry {
        provider: "openai",
        runner: "codex",
        mode: "apiKey",
        inject: &["CODEX_API_KEY"],
        scrub: &[],
        keychain_account: Some("orrery/openai/api-key"),
    },
    MatrixEntry {
        provider: "openai",
        runner: "aider",
        mode: "apiKey",
        inject: &["OPENAI_API_KEY"],
        scrub: &[],
        keychain_account: Some("orrery/openai/api-key"),
    },
    MatrixEntry {
        provider: "google",
        runner: "aider",
        mode: "apiKey",
        inject: &["GEMINI_API_KEY"],
        scrub: &[],
        keychain_account: Some("orrery/google/api-key"),
    },
    MatrixEntry {
        provider: "openrouter",
        runner: "claude",
        mode: "gateway",
        inject: &["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"],
        scrub: &["ANTHROPIC_API_KEY"],
        keychain_account: Some("orrery/openrouter/api-key"),
    },
    MatrixEntry {
        provider: "openrouter",
        runner: "aider",
        mode: "gateway",
        inject: &["OPENROUTER_API_KEY"],
        scrub: &[],
        keychain_account: Some("orrery/openrouter/api-key"),
    },
    MatrixEntry {
        provider: "bedrock",
        runner: "claude",
        mode: "cloud",
        inject: &["CLAUDE_CODE_USE_BEDROCK", "AWS_REGION"],
        scrub: &["ANTHROPIC_API_KEY"],
        keychain_account: Some("orrery/bedrock/creds"),
    },
    MatrixEntry {
        provider: "vertex",
        runner: "claude",
        mode: "cloud",
        inject: &["CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID"],
        scrub: &["ANTHROPIC_API_KEY"],
        keychain_account: Some("orrery/vertex/creds"),
    },
    MatrixEntry {
        provider: "local",
        runner: "aider",
        mode: "local",
        inject: &["OLLAMA_API_BASE"],
        scrub: &[],
        keychain_account: None,
    },
];

/// Look up the rule for a (provider, runner, mode) triple; `None` = unsupported.
fn provider_rule(provider: &str, runner: &str, mode: &str) -> Option<&'static MatrixEntry> {
    PROVIDER_MATRIX
        .iter()
        .find(|e| e.provider == provider && e.runner == runner && e.mode == mode)
}

/// The VALUE source for one named inject env var. The matrix only NAMES env vars; this maps each
/// name to where its value comes from — the keychain secret, the instance's baseUrl/region, or a
/// literal cloud flag. Returns `None` (skip the var) when the source is empty/absent — e.g.
/// `ANTHROPIC_VERTEX_PROJECT_ID`, which has no field in the v1 instance model.
fn value_for(
    name: &str,
    base_url: Option<&str>,
    region: Option<&str>,
    secret: Option<&str>,
) -> Option<String> {
    let nonempty = |o: Option<&str>| {
        o.map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
    };
    match name {
        // Secret goes into the provider's primary key(s) — read from the keychain, never here.
        "ANTHROPIC_API_KEY" | "CODEX_API_KEY" | "OPENAI_API_KEY" | "OPENROUTER_API_KEY"
        | "GEMINI_API_KEY" | "ANTHROPIC_AUTH_TOKEN" => nonempty(secret),
        "ANTHROPIC_BASE_URL" | "OPENAI_BASE_URL" | "OLLAMA_API_BASE" => nonempty(base_url),
        "AWS_REGION" | "CLOUD_ML_REGION" => nonempty(region),
        "CLAUDE_CODE_USE_BEDROCK" | "CLAUDE_CODE_USE_VERTEX" => Some("1".to_string()),
        _ => None,
    }
}

/// Pure BYOK env builder (the auditable core, unit-tested): given a resolved instance and a secret
/// lookup, return `(inject pairs, scrub keys)`.
///
/// Fallback rules (all map to `([], [])` = inherit today's env):
///   * unsupported (provider, runner, mode);
///   * a secret-backed mode (`keychainAccount = Some`) whose secret is NOT stored — we would
///     otherwise scrub the inherited keys and inject nothing, leaving the runner with no
///     credentials at all, which is strictly worse than doing nothing.
/// No-secret modes (subscription/cloud/local) always apply their inject + scrub.
fn build_byok_env(
    provider: &str,
    runner: &str,
    mode: &str,
    base_url: Option<&str>,
    region: Option<&str>,
    secret_lookup: impl Fn(&str) -> Option<String>,
) -> (Vec<(String, String)>, Vec<String>) {
    let rule = match provider_rule(provider, runner, mode) {
        Some(r) => r,
        None => return (Vec::new(), Vec::new()),
    };
    let secret = match rule.keychain_account {
        Some(acct) => match secret_lookup(acct) {
            Some(s) => Some(s),
            None => return (Vec::new(), Vec::new()),
        },
        None => None,
    };
    let mut inject: Vec<(String, String)> = Vec::new();
    for name in rule.inject {
        if let Some(val) = value_for(name, base_url, region, secret.as_deref()) {
            inject.push(((*name).to_string(), val));
        }
    }
    let scrub: Vec<String> = rule.scrub.iter().map(|s| (*s).to_string()).collect();
    (inject, scrub)
}

/// Extract the default `ProviderInstance` object from a parsed settings tree, if one is selected.
fn default_instance(settings: &Value) -> Option<Value> {
    let ai = settings.get("ai")?;
    let default_id = ai.get("defaultInstanceId")?.as_str()?;
    let instances = ai.get("instances")?.as_array()?;
    instances
        .iter()
        .find(|i| i.get("id").and_then(|v| v.as_str()) == Some(default_id))
        .cloned()
}

/// Resolve the spawn-time BYOK env from `settings.json`'s DEFAULT instance — callable WITHOUT an
/// `AppHandle` (reads the config-dir OnceLock). Returns `(inject pairs, scrub keys)`; empty/empty
/// when there is no default instance or the combo is unsupported (fall back to inherited env =
/// today's behavior). NEVER logs a secret. `control::spawn_detached` applies the result.
pub fn byok_env() -> (Vec<(String, String)>, Vec<String>) {
    let settings = match read_settings_value() {
        Some(v) => v,
        None => return (Vec::new(), Vec::new()),
    };
    let inst = match default_instance(&settings) {
        Some(i) => i,
        None => return (Vec::new(), Vec::new()),
    };
    let field = |k: &str| inst.get(k).and_then(|v| v.as_str());
    build_byok_env(
        field("provider").unwrap_or(""),
        field("runner").unwrap_or(""),
        field("mode").unwrap_or(""),
        field("baseUrl"),
        field("region"),
        keychain_get,
    )
}

// ---------------------------------------------------------------------------
// byok_auth_status — a CHEAP reachability check (PATH probe or keychain presence),
// never a live network call (v1).
// ---------------------------------------------------------------------------

/// Result of a BYOK auth probe for one instance (mirrors `ByokAuthStatus` in contract.ts).
#[derive(Serialize)]
pub struct ByokAuthStatus {
    runner: String,
    provider: String,
    mode: String,
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    detail: Option<String>,
}

/// The CLI binary a runner spawns (for the PATH probe).
fn runner_binary(runner: &str) -> &str {
    match runner {
        "claude" => "claude",
        "codex" => "codex",
        "aider" => "aider",
        other => other,
    }
}

/// Is `name` an executable somewhere on `PATH`? A cheap existence scan — never executes anything.
fn binary_on_path(name: &str) -> bool {
    let path = match std::env::var_os("PATH") {
        Some(p) => p,
        None => return false,
    };
    #[cfg(windows)]
    let candidates: Vec<String> = vec![
        format!("{name}.exe"),
        format!("{name}.cmd"),
        format!("{name}.bat"),
        name.to_string(),
    ];
    #[cfg(not(windows))]
    let candidates: Vec<String> = vec![name.to_string()];
    std::env::split_paths(&path).any(|dir| candidates.iter().any(|n| dir.join(n).is_file()))
}

#[tauri::command]
pub fn byok_auth_status(instance: Value) -> ByokAuthStatus {
    let field = |k: &str| instance.get(k).and_then(|v| v.as_str()).unwrap_or("");
    let runner = field("runner").to_string();
    let provider = field("provider").to_string();
    let mode = field("mode").to_string();

    let (ok, detail) = match mode.as_str() {
        // No secret needed — reachable if the runner binary is on PATH.
        "subscription" | "cloud" | "local" => {
            let present = binary_on_path(runner_binary(&runner));
            (
                present,
                if present {
                    None
                } else {
                    Some(format!("{} not found on PATH", runner_binary(&runner)))
                },
            )
        }
        // Secret-backed — reachable if a keychain secret exists for the matrix account.
        "apiKey" | "gateway" => match provider_rule(&provider, &runner, &mode)
            .and_then(|r| r.keychain_account)
        {
            Some(acct) => {
                let has = keychain_get(acct).is_some();
                (has, if has { None } else { Some("no key stored".to_string()) })
            }
            None => (false, Some("unsupported provider/runner/mode".to_string())),
        },
        other => (false, Some(format!("unknown mode {other:?}"))),
    };

    ByokAuthStatus {
        runner,
        provider,
        mode,
        ok,
        detail,
    }
}

// ---------------------------------------------------------------------------
// export / import — defensively strip any secret fields (settings.json has none
// by design; this is belt-and-braces so an export can never leak one).
// ---------------------------------------------------------------------------

/// Recursively drop any object key whose (case-insensitive) name is a known secret field. Matched
/// by EXACT name so the legit `hasSecret` boolean mirror survives.
fn strip_secrets(value: &mut Value) {
    match value {
        Value::Object(map) => {
            const DENY: &[&str] = &[
                "secret",
                "apikey",
                "api_key",
                "password",
                "token",
                "authtoken",
                "auth_token",
            ];
            map.retain(|k, _| !DENY.contains(&k.to_lowercase().as_str()));
            for v in map.values_mut() {
                strip_secrets(v);
            }
        }
        Value::Array(arr) => {
            for v in arr.iter_mut() {
                strip_secrets(v);
            }
        }
        _ => {}
    }
}

#[tauri::command]
pub fn export_settings(path: String, contents: Option<String>) -> Result<(), String> {
    // Prefer the caller's in-memory payload (already secret-free, and fresher than the
    // autosaved file); fall back to the on-disk settings.json when none is supplied.
    let text = match contents {
        Some(c) => c,
        None => {
            let src = settings_path().ok_or_else(|| "settings path not initialized".to_string())?;
            std::fs::read_to_string(&src).map_err(|e| format!("read settings: {e}"))?
        }
    };
    let mut value: Value =
        serde_json::from_str(&text).map_err(|e| format!("parse settings: {e}"))?;
    strip_secrets(&mut value); // defensive — settings.json carries no secrets by design
    let out = serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?;
    std::fs::write(&path, out).map_err(|e| format!("write {path:?}: {e}"))
}

/// Top-level keys of the `Settings` shape (schema.ts). Import drops anything else.
const ALLOWED_TOP_KEYS: &[&str] = &[
    "version",
    "general",
    "appearance",
    "loopDefaults",
    "notifications",
    "ai",
    "diagnostics",
];

/// Read + parse + sanitize an exported settings file. Validates `version == 1`, drops unknown
/// top-level keys, strips any secret fields, and returns the sanitized object for the JS store to
/// merge. Does NOT write — the store persists.
#[tauri::command]
pub fn import_settings(path: String) -> Result<Value, String> {
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read {path:?}: {e}"))?;
    let value: Value = serde_json::from_str(&text).map_err(|e| format!("parse {path:?}: {e}"))?;
    let obj = value
        .as_object()
        .ok_or_else(|| "settings file must be a JSON object".to_string())?;
    match obj.get("version").and_then(|v| v.as_u64()) {
        Some(1) => {}
        _ => return Err("unsupported settings version (expected 1)".to_string()),
    }
    let mut out = serde_json::Map::new();
    for key in ALLOWED_TOP_KEYS {
        if let Some(v) = obj.get(*key) {
            out.insert((*key).to_string(), v.clone());
        }
    }
    let mut sanitized = Value::Object(out);
    strip_secrets(&mut sanitized);
    Ok(sanitized)
}

// ---------------------------------------------------------------------------
// watch_settings / unwatch_settings — a debounced `notify` watcher on settings.json.
// Mirrors control::watch_run's lifecycle: one live watcher, replaced/reaped on
// re-invocation, torn down on unwatch. Emits the parsed JSON on EXTERNAL change.
// ---------------------------------------------------------------------------

struct SettingsWatch {
    stop: Arc<AtomicBool>,
    join: std::thread::JoinHandle<()>,
}

static SETTINGS_WATCHER: OnceLock<Mutex<Option<SettingsWatch>>> = OnceLock::new();

fn settings_watcher_slot() -> &'static Mutex<Option<SettingsWatch>> {
    SETTINGS_WATCHER.get_or_init(|| Mutex::new(None))
}

/// Install `next` as the current watcher, signalling + reaping any previous one first (at most one
/// live settings watcher, like `control::replace_watcher`).
fn replace_settings_watcher(next: SettingsWatch) {
    let prev = {
        let mut slot = settings_watcher_slot()
            .lock()
            .unwrap_or_else(|p| p.into_inner());
        slot.replace(next)
    };
    if let Some(prev) = prev {
        prev.stop.store(true, Ordering::Relaxed);
        let _ = prev.join.join();
    }
}

#[tauri::command]
pub fn watch_settings(channel: tauri::ipc::Channel<Value>) -> Result<(), String> {
    let path = settings_path().ok_or_else(|| "settings path not initialized".to_string())?;
    let watch_dir = path
        .parent()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let _ = std::fs::create_dir_all(&watch_dir);

    let stop = Arc::new(AtomicBool::new(false));
    let stop_thread = stop.clone();
    let path2 = path.clone();

    let join = std::thread::spawn(move || {
        let (tx, rx) = mpsc::channel();
        // 200ms debounce — the store writes atomically (temp + rename); we watch the DIR (not the
        // file) so a rename-in-place still fires. The JS side also debounces its merge.
        let mut debouncer = match new_debouncer(
            Duration::from_millis(200),
            move |res: DebounceEventResult| {
                let _ = tx.send(res);
            },
        ) {
            Ok(d) => d,
            Err(_) => return,
        };
        if debouncer
            .watcher()
            .watch(&watch_dir, RecursiveMode::NonRecursive)
            .is_err()
        {
            return;
        }
        // Seed the mtime so the first real change is detected — and so we never emit our OWN /
        // no-op writes (an event whose mtime equals what we last saw is skipped).
        let mut last_mtime: Option<SystemTime> = std::fs::metadata(&path2)
            .ok()
            .and_then(|m| m.modified().ok());

        loop {
            if stop_thread.load(Ordering::Relaxed) {
                break;
            }
            match rx.recv_timeout(Duration::from_millis(200)) {
                Ok(_) => {
                    let mtime = std::fs::metadata(&path2)
                        .ok()
                        .and_then(|m| m.modified().ok());
                    if mtime == last_mtime {
                        continue; // unchanged content (or our own no-op) — skip
                    }
                    last_mtime = mtime;
                    if let Ok(text) = std::fs::read_to_string(&path2) {
                        if let Ok(v) = serde_json::from_str::<Value>(&text) {
                            // A failed send means the receiving Channel is gone — stop.
                            if channel.send(v).is_err() {
                                break;
                            }
                        }
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => continue,
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }
        }
    });

    replace_settings_watcher(SettingsWatch { stop, join });
    Ok(())
}

#[tauri::command]
pub fn unwatch_settings() -> Result<(), String> {
    let prev = {
        let mut slot = settings_watcher_slot()
            .lock()
            .unwrap_or_else(|p| p.into_inner());
        slot.take()
    };
    if let Some(prev) = prev {
        prev.stop.store(true, Ordering::Relaxed);
        let _ = prev.join.join();
    }
    Ok(())
}

// ===========================================================================
// Tests — the matrix mapping (inject + scrub) and resolve_loops_dir precedence.
// No keychain or config-dir OnceLock is touched: the pure cores take a secret
// lookup closure / a parsed Value, so nothing hits the real OS keystore.
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    fn inject_set(v: &[(String, String)]) -> HashSet<(String, String)> {
        v.iter().cloned().collect()
    }
    fn scrub_set(v: &[String]) -> HashSet<String> {
        v.iter().cloned().collect()
    }
    fn s(pairs: &[(&str, &str)]) -> HashSet<(String, String)> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }
    fn ss(keys: &[&str]) -> HashSet<String> {
        keys.iter().map(|k| k.to_string()).collect()
    }

    // A secret lookup that always yields the same value (any account) — for supported apiKey/
    // gateway/cloud combos where a secret is required.
    fn with_secret(val: &'static str) -> impl Fn(&str) -> Option<String> {
        move |_acct: &str| Some(val.to_string())
    }
    // A secret lookup that yields nothing — no secret stored.
    fn no_secret(_acct: &str) -> Option<String> {
        None
    }

    #[test]
    fn subscription_injects_nothing_but_scrubs_the_keys() {
        // anthropic/claude/subscription: no secret needed; empty inject, scrub the three keys.
        let (inject, scrub) =
            build_byok_env("anthropic", "claude", "subscription", None, None, no_secret);
        assert!(inject.is_empty(), "subscription injects nothing: {inject:?}");
        assert_eq!(
            scrub_set(&scrub),
            ss(&["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"]),
        );
    }

    #[test]
    fn anthropic_apikey_injects_key_and_scrubs_base_and_token() {
        let (inject, scrub) = build_byok_env(
            "anthropic",
            "claude",
            "apiKey",
            None,
            None,
            with_secret("sk-test"),
        );
        assert_eq!(inject_set(&inject), s(&[("ANTHROPIC_API_KEY", "sk-test")]));
        assert_eq!(scrub_set(&scrub), ss(&["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"]));
    }

    #[test]
    fn openrouter_gateway_injects_base_and_token_and_scrubs_api_key() {
        let (inject, scrub) = build_byok_env(
            "openrouter",
            "claude",
            "gateway",
            Some("https://openrouter.ai/api/v1"),
            None,
            with_secret("or-key"),
        );
        assert_eq!(
            inject_set(&inject),
            s(&[
                ("ANTHROPIC_BASE_URL", "https://openrouter.ai/api/v1"),
                ("ANTHROPIC_AUTH_TOKEN", "or-key"),
            ]),
        );
        assert_eq!(scrub_set(&scrub), ss(&["ANTHROPIC_API_KEY"]));
    }

    #[test]
    fn bedrock_cloud_injects_flag_and_region_and_scrubs_api_key() {
        // cloud mode still has a keychainAccount (creds) — secret required to apply.
        let (inject, scrub) = build_byok_env(
            "bedrock",
            "claude",
            "cloud",
            None,
            Some("us-east-1"),
            with_secret("aws-creds"),
        );
        assert_eq!(
            inject_set(&inject),
            s(&[("CLAUDE_CODE_USE_BEDROCK", "1"), ("AWS_REGION", "us-east-1")]),
        );
        assert_eq!(scrub_set(&scrub), ss(&["ANTHROPIC_API_KEY"]));
    }

    #[test]
    fn unsupported_combo_is_empty_and_empty() {
        // anthropic/codex/apiKey is not in the matrix → inherit env (no inject, no scrub).
        let (inject, scrub) =
            build_byok_env("anthropic", "codex", "apiKey", None, None, with_secret("x"));
        assert!(inject.is_empty() && scrub.is_empty(), "{inject:?} / {scrub:?}");
    }

    #[test]
    fn secret_backed_mode_without_secret_falls_back_to_inherited_env() {
        // Supported combo, but no stored secret → empty/empty rather than scrub-with-no-key.
        let (inject, scrub) =
            build_byok_env("anthropic", "claude", "apiKey", None, None, no_secret);
        assert!(inject.is_empty() && scrub.is_empty(), "{inject:?} / {scrub:?}");
    }

    #[test]
    fn vertex_skips_project_id_with_no_source_but_injects_flag_and_region() {
        // ANTHROPIC_VERTEX_PROJECT_ID has no field in the v1 instance model → skipped, not
        // injected empty. The flag + region still apply.
        let (inject, _scrub) = build_byok_env(
            "vertex",
            "claude",
            "cloud",
            None,
            Some("us-central1"),
            with_secret("gcp-creds"),
        );
        assert_eq!(
            inject_set(&inject),
            s(&[("CLAUDE_CODE_USE_VERTEX", "1"), ("CLOUD_ML_REGION", "us-central1")]),
        );
    }

    #[test]
    fn resolve_loops_dir_prefers_override_else_default() {
        // Write a temp settings.json with an explicit loopsDir → returned verbatim.
        let mut p = std::env::temp_dir();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        p.push(format!("orrery-settings-test-{nanos}.json"));

        std::fs::write(&p, r#"{"version":1,"general":{"loopsDir":"D:/custom/loops"}}"#).unwrap();
        let parsed: Value =
            serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(resolve_loops_dir_from(Some(&parsed)), "D:/custom/loops");

        // Null override → the built-in default.
        std::fs::write(&p, r#"{"version":1,"general":{"loopsDir":null}}"#).unwrap();
        let parsed2: Value =
            serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(resolve_loops_dir_from(Some(&parsed2)), builtin_default_loops_dir());

        // Empty string → default (whitespace-only counts as unset).
        std::fs::write(&p, r#"{"version":1,"general":{"loopsDir":"  "}}"#).unwrap();
        let parsed3: Value =
            serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(resolve_loops_dir_from(Some(&parsed3)), builtin_default_loops_dir());

        // Missing settings entirely → default.
        assert_eq!(resolve_loops_dir_from(None), builtin_default_loops_dir());

        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn import_sanitizes_version_unknown_keys_and_secrets() {
        // A valid v1 blob with a stray top-level key and a planted secret field.
        let raw = r#"{
            "version": 1,
            "general": { "loopsDir": null },
            "ai": { "instances": [ { "id": "a", "hasSecret": true, "apiKey": "LEAK" } ] },
            "bogusTop": 42
        }"#;
        let mut p = std::env::temp_dir();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        p.push(format!("orrery-import-test-{nanos}.json"));
        std::fs::write(&p, raw).unwrap();

        let out = import_settings(p.to_string_lossy().to_string()).unwrap();
        let obj = out.as_object().unwrap();
        assert!(!obj.contains_key("bogusTop"), "unknown top-level key dropped");
        assert!(obj.contains_key("general") && obj.contains_key("ai"));
        // the planted secret is stripped, the legit hasSecret mirror survives
        let inst = &out["ai"]["instances"][0];
        assert!(inst.get("apiKey").is_none(), "secret field stripped");
        assert_eq!(inst["hasSecret"], serde_json::json!(true));

        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn import_rejects_wrong_version() {
        let mut p = std::env::temp_dir();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        p.push(format!("orrery-import-badver-{nanos}.json"));
        std::fs::write(&p, r#"{"version":2,"general":{}}"#).unwrap();
        assert!(import_settings(p.to_string_lossy().to_string()).is_err());
        let _ = std::fs::remove_file(&p);
    }
}
