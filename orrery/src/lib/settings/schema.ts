// settings/schema.ts — the FOUNDATIONAL CONTRACT for the Settings feature (Phase 0).
//
// This module is the single source of truth five parallel workstreams import:
//   • the shape of persisted settings (`Settings` + `DEFAULTS`),
//   • the BYOK provider model (`ProviderInstance` + `PROVIDER_MATRIX`) that the
//     Rust `byok_env()` MIRRORS at spawn time,
//   • the render/search/reset metadata (`REGISTRY` + `SettingMeta`).
//
// It is pure + framework-free (no Svelte, no Tauri) so it can be unit-checked and
// reused by the store (WS-A), the panels (WS-B), and the backend contract (WS-E).
// Existing app unions are REUSED by import — never redefined here. Where the
// codebase exports no suitable union, a local type is defined and clearly marked.

import type { Model, RestState } from '../types';
import type { BlueprintId, EngineConfig } from '../blueprints';

// ─── Reused / locally-defined leaf unions ───────────────────────────────────

/** Claude Code permission mode — reused verbatim from the engine config shape. */
export type PermissionMode = EngineConfig['permissionMode'];

/**
 * Reasoning-effort tier for a loop's agent. The codebase treats effort as a free
 * `string` (BMAD phase efforts / QA `effort`) and exports no union, so this is
 * defined LOCALLY as the canonical Settings-side tier list.
 */
export type EffortLevel = 'low' | 'medium' | 'high' | 'xhigh';

/**
 * The rest-states that can raise an unattended alert. REUSES the app's `RestState`
 * union (types.ts) minus its running-time `null`, so the alert set can never drift
 * from the canonical palette. Maps to the human intents: handoff-beacon=needs-input,
 * quota-frost=quota, failed-dark=failed, certified-done=done, stopped-ember=stopped.
 */
export type AlertState = NonNullable<RestState>;

// ─── BYOK: runners, providers, auth modes ───────────────────────────────────

/** The CLI agent a loop spawns. `claude` today; `codex`/`aider` are BYOK targets. */
export type RunnerId = 'claude' | 'codex' | 'aider';

/** The model/inference provider behind a runner. */
export type ProviderId =
  | 'anthropic'
  | 'openai'
  | 'google'
  | 'openrouter'
  | 'bedrock'
  | 'vertex'
  | 'local';

/** How the runner authenticates against the provider. */
export type AuthMode = 'subscription' | 'apiKey' | 'gateway' | 'cloud' | 'local';

/**
 * One configured (runner + provider + auth) credential profile. The SECRET itself
 * NEVER lives here — it is stored in the OS keychain under the matrix's
 * `keychainAccount`; `hasSecret` is a UI-only mirror of the backend `keychain_has`.
 */
export interface ProviderInstance {
  id: string;
  name: string;
  runner: RunnerId;
  provider: ProviderId;
  mode: AuthMode;
  baseUrl?: string;
  region?: string;
  /** GCP project id — Vertex cloud only (ANTHROPIC_VERTEX_PROJECT_ID at spawn). */
  projectId?: string;
  defaultModel?: string;
  /** mirror of keychain_has for this instance's account; the secret never lives here. */
  hasSecret?: boolean;
}

// ─── The provider matrix (single source of truth the Rust byok_env mirrors) ──
// For each supported (provider, runner, mode) triple: which env vars the backend
// INJECTS (values sourced from user input — key value / base URL / region — at
// spawn), which it SCRUBS from the inherited env, the keychain ACCOUNT the secret
// lives under (null = no secret needed), and whether the combo is SUPPORTED.
// The matrix only NAMES env vars; it never carries a secret value.

export interface ProviderRule {
  /** env vars the backend sets at spawn (values come from user input, not here). */
  inject: string[];
  /** env vars the backend removes from the inherited environment. */
  scrub: string[];
  /** OS-keychain account the secret is stored under, or null when none is needed. */
  keychainAccount: string | null;
  supported: boolean;
}

export interface ProviderMatrixEntry extends ProviderRule {
  provider: ProviderId;
  runner: RunnerId;
  mode: AuthMode;
}

/** Every SUPPORTED (provider, runner, mode) combination. Anything absent is unsupported. */
export const PROVIDER_MATRIX: ProviderMatrixEntry[] = [
  {
    provider: 'anthropic',
    runner: 'claude',
    mode: 'subscription',
    inject: [],
    scrub: ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL'],
    keychainAccount: null,
    supported: true,
  },
  {
    provider: 'anthropic',
    runner: 'claude',
    mode: 'apiKey',
    inject: ['ANTHROPIC_API_KEY'],
    scrub: ['ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN'],
    keychainAccount: 'orrery/anthropic/api-key',
    supported: true,
  },
  {
    provider: 'anthropic',
    runner: 'aider',
    mode: 'apiKey',
    inject: ['ANTHROPIC_API_KEY'],
    scrub: [],
    keychainAccount: 'orrery/anthropic/api-key',
    supported: true,
  },
  {
    provider: 'openai',
    runner: 'codex',
    mode: 'subscription',
    inject: [],
    scrub: ['OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL'],
    keychainAccount: null,
    supported: true,
  },
  {
    provider: 'openai',
    runner: 'codex',
    mode: 'apiKey',
    inject: ['CODEX_API_KEY'],
    scrub: [],
    keychainAccount: 'orrery/openai/api-key',
    supported: true,
  },
  {
    provider: 'openai',
    runner: 'aider',
    mode: 'apiKey',
    inject: ['OPENAI_API_KEY'],
    scrub: [],
    keychainAccount: 'orrery/openai/api-key',
    supported: true,
  },
  {
    provider: 'google',
    runner: 'aider',
    mode: 'apiKey',
    inject: ['GEMINI_API_KEY'],
    scrub: [],
    keychainAccount: 'orrery/google/api-key',
    supported: true,
  },
  {
    provider: 'openrouter',
    runner: 'claude',
    mode: 'gateway',
    inject: ['ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN'],
    scrub: ['ANTHROPIC_API_KEY'],
    keychainAccount: 'orrery/openrouter/api-key',
    supported: true,
  },
  {
    provider: 'openrouter',
    runner: 'aider',
    mode: 'gateway',
    inject: ['OPENROUTER_API_KEY'],
    scrub: [],
    keychainAccount: 'orrery/openrouter/api-key',
    supported: true,
  },
  // Cloud modes carry NO keychain account: bedrock/vertex authenticate via the ambient
  // AWS/GCP credential chain (aws configure / gcloud ADC), which the spawned child inherits.
  // Only the routing flag + region/project are injected — a stored secret would be a dead
  // gate that silently disabled the mode for ambient-auth users (mirrors settings.rs).
  {
    provider: 'bedrock',
    runner: 'claude',
    mode: 'cloud',
    inject: ['CLAUDE_CODE_USE_BEDROCK', 'AWS_REGION'],
    scrub: ['ANTHROPIC_API_KEY'],
    keychainAccount: null,
    supported: true,
  },
  {
    provider: 'vertex',
    runner: 'claude',
    mode: 'cloud',
    inject: ['CLAUDE_CODE_USE_VERTEX', 'CLOUD_ML_REGION', 'ANTHROPIC_VERTEX_PROJECT_ID'],
    scrub: ['ANTHROPIC_API_KEY'],
    keychainAccount: null,
    supported: true,
  },
  {
    provider: 'local',
    runner: 'aider',
    mode: 'local',
    inject: ['OLLAMA_API_BASE'],
    scrub: [],
    keychainAccount: null,
    supported: true,
  },
];

/** The rule returned for any (provider, runner, mode) not present in the matrix. */
const UNSUPPORTED_RULE: ProviderRule = {
  inject: [],
  scrub: [],
  keychainAccount: null,
  supported: false,
};

/**
 * Query the matrix by (provider, runner, mode). Returns the exact rule for a
 * supported combo, or an `{ supported: false }` rule for every other triple
 * (e.g. anthropic|codex|*, openai|claude|*, google|claude|*, aider|subscription).
 * This makes the 105-cell space exhaustive by construction — the Rust `byok_env()`
 * mirrors the same lookup.
 */
export function providerRule(
  provider: ProviderId,
  runner: RunnerId,
  mode: AuthMode,
): ProviderRule {
  const hit = PROVIDER_MATRIX.find(
    (e) => e.provider === provider && e.runner === runner && e.mode === mode,
  );
  if (!hit) return UNSUPPORTED_RULE;
  return {
    inject: hit.inject,
    scrub: hit.scrub,
    keychainAccount: hit.keychainAccount,
    supported: hit.supported,
  };
}

// ─── The persisted Settings shape ───────────────────────────────────────────

export interface Settings {
  version: 1;
  general: {
    /** override for the build-time DEFAULT_LOOPS_DIR; null = use the resolved default. */
    loopsDir: string | null;
    cosmosPollMs: number;
    lanPort: number;
    confirmDestructive: boolean;
    startInAmbient: boolean;
  };
  appearance: {
    theme: 'light' | 'dark' | 'system';
    motion: 'system' | 'full' | 'reduced';
    density: 'comfortable' | 'compact';
    grain: boolean;
  };
  loopDefaults: {
    ceilingUsd: number;
    runner: RunnerId;
    model: Model;
    effort: EffortLevel;
    permissionMode: PermissionMode;
    blueprint: BlueprintId;
  };
  notifications: {
    unattendedAlerts: boolean;
    alertOn: AlertState[];
    sound: boolean;
    quotaResumeToast: boolean;
  };
  ai: {
    instances: ProviderInstance[];
    defaultInstanceId: string | null;
    fallbackInstanceId: string | null;
  };
  diagnostics: {
    logLevel: 'info' | 'debug';
    telemetry: false;
  };
}

// ─── DEFAULTS — mirror today's hardcoded values EXACTLY ──────────────────────
// Numeric defaults reference the real source constants (cited inline). Note: none
// of the three replaced constants is exported (COSMOS_POLL_MS + DEFAULT_CEILING are
// module-local; DEFAULT_PORT is Rust), and this file may not edit them, so the
// values are duplicated here with the source cited rather than imported.

export const DEFAULTS: Settings = {
  version: 1,
  general: {
    loopsDir: null, // null = resolve DEFAULT_LOOPS_DIR (paths.ts) at runtime
    cosmosPollMs: 5000, // = COSMOS_POLL_MS (routes/+page.svelte ~L208)
    lanPort: 8787, // = DEFAULT_PORT (src-tauri/src/lan.rs ~L170)
    confirmDestructive: true,
    startInAmbient: false,
  },
  appearance: {
    theme: 'dark',
    motion: 'system',
    density: 'comfortable',
    grain: true,
  },
  loopDefaults: {
    ceilingUsd: 80, // = DEFAULT_CEILING (stores/cosmos.svelte.ts ~L98)
    runner: 'claude',
    model: 'sonnet', // the default execute tier (blueprints deriveFromDials)
    effort: 'high',
    permissionMode: 'acceptEdits', // composeEngine's default for non-high autonomy
    blueprint: 'grind', // TuningConsole's default blueprintId
  },
  notifications: {
    unattendedAlerts: true,
    alertOn: ['handoff-beacon', 'quota-frost', 'failed-dark', 'certified-done'],
    sound: false,
    quotaResumeToast: true,
  },
  ai: {
    instances: [],
    defaultInstanceId: null,
    fallbackInstanceId: null,
  },
  diagnostics: {
    logLevel: 'info',
    telemetry: false,
  },
};

// ─── Settings metadata (render · search · reset) ────────────────────────────

export type ApplyMode = 'instant' | 'restart';
export type Scope = 'user' | 'workspace' | 'both';
export type ControlKind =
  | 'toggle'
  | 'seg'
  | 'slider'
  | 'text'
  | 'number'
  | 'select'
  | 'path'
  | 'readonly';

export interface SettingMeta {
  /** dotted path into `Settings`, e.g. 'general.lanPort' — also the store get/set key. */
  key: string;
  category: keyof Settings;
  label: string;
  description: string;
  keywords: string[];
  control: ControlKind;
  options?: { value: string; label: string }[];
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  apply: ApplyMode;
  scope: Scope;
  /** true when a Rust command must be invoked to apply/persist the change. */
  backend: boolean;
  /** the hardcoded constant this setting retires, for the migration note. */
  replaces?: string;
  validate?: (v: unknown) => string | null;
}

// reusable option lists
const THEME_OPTS = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
];
const MOTION_OPTS = [
  { value: 'system', label: 'System' },
  { value: 'full', label: 'Full' },
  { value: 'reduced', label: 'Reduced' },
];
const DENSITY_OPTS = [
  { value: 'comfortable', label: 'Comfortable' },
  { value: 'compact', label: 'Compact' },
];
const RUNNER_OPTS = [
  { value: 'claude', label: 'Claude Code' },
  { value: 'codex', label: 'Codex' },
  { value: 'aider', label: 'Aider' },
];
const MODEL_OPTS = [
  { value: 'haiku', label: 'Haiku' },
  { value: 'sonnet', label: 'Sonnet' },
  { value: 'opus', label: 'Opus' },
];
const EFFORT_OPTS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra high' },
];
const PERMISSION_OPTS = [
  { value: 'acceptEdits', label: 'Accept edits' },
  { value: 'plan', label: 'Plan' },
  { value: 'default', label: 'Default' },
  { value: 'bypassPermissions', label: 'Bypass permissions' },
];
const BLUEPRINT_OPTS = [
  { value: 'grind', label: 'Grind' },
  { value: 'sprint', label: 'Sprint' },
  { value: 'explore', label: 'Explore' },
  { value: 'custom', label: 'Custom' },
];
const ALERT_OPTS = [
  { value: 'handoff-beacon', label: 'Needs input' },
  { value: 'quota-frost', label: 'Quota wait' },
  { value: 'failed-dark', label: 'Failed' },
  { value: 'certified-done', label: 'Done' },
  { value: 'stopped-ember', label: 'Stopped' },
];
const LOGLEVEL_OPTS = [
  { value: 'info', label: 'Info' },
  { value: 'debug', label: 'Debug' },
];

// validators
const vPort = (v: unknown): string | null => {
  const n = Number(v);
  return Number.isInteger(n) && n >= 1024 && n <= 65535
    ? null
    : 'Port must be a whole number between 1024 and 65535.';
};
const vPoll = (v: unknown): string | null => {
  const n = Number(v);
  return Number.isFinite(n) && n >= 1000 ? null : 'Poll interval must be at least 1000 ms.';
};
const vCeiling = (v: unknown): string | null => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? null : 'Cost ceiling must be greater than 0.';
};
const vLoopsDir = (v: unknown): string | null =>
  v === null || (typeof v === 'string' && v.trim().length > 0)
    ? null
    : 'Loops folder cannot be empty.';

/**
 * One entry per SCALAR setting across all six categories. The AI provider-instance
 * list is dynamic and NOT enumerated here; a single readonly `ai` anchor lets search
 * surface the AI tab. `replaces` marks a setting that retires a hardcoded constant.
 */
export const REGISTRY: SettingMeta[] = [
  // ── general ───────────────────────────────────────────────────────────────
  {
    key: 'general.loopsDir',
    category: 'general',
    label: 'Loops folder',
    description: 'Where loops are stored and discovered. Empty uses the built-in default.',
    keywords: ['loops', 'directory', 'folder', 'path', 'workspace'],
    control: 'path',
    apply: 'restart',
    scope: 'user',
    backend: true,
    replaces: 'DEFAULT_LOOPS_DIR (paths.ts)',
    validate: vLoopsDir,
  },
  {
    key: 'general.cosmosPollMs',
    category: 'general',
    label: 'Cosmos refresh interval',
    description: 'How often the Cosmos view re-scans loops for changes.',
    keywords: ['poll', 'refresh', 'interval', 'cosmos', 'scan'],
    control: 'number',
    min: 1000,
    step: 500,
    unit: 'ms',
    apply: 'instant',
    scope: 'user',
    backend: false,
    replaces: 'COSMOS_POLL_MS (routes/+page.svelte)',
    validate: vPoll,
  },
  {
    key: 'general.lanPort',
    category: 'general',
    label: 'LAN server port',
    description: 'Port the local LAN companion server listens on.',
    keywords: ['lan', 'port', 'server', 'network', 'phone', 'companion'],
    control: 'number',
    min: 1024,
    max: 65535,
    step: 1,
    apply: 'restart',
    scope: 'user',
    backend: true,
    replaces: 'DEFAULT_PORT (src-tauri/src/lan.rs)',
    validate: vPort,
  },
  {
    key: 'general.confirmDestructive',
    category: 'general',
    label: 'Confirm destructive actions',
    description: 'Ask before deleting a loop or discarding a run.',
    keywords: ['confirm', 'destructive', 'delete', 'safety', 'prompt'],
    control: 'toggle',
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  {
    key: 'general.startInAmbient',
    category: 'general',
    label: 'Start in ambient view',
    description: 'Open a loop in the full-screen Planetarium (ambient) view.',
    keywords: ['ambient', 'planetarium', 'startup', 'default', 'view'],
    control: 'toggle',
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  // ── appearance ────────────────────────────────────────────────────────────
  {
    key: 'appearance.theme',
    category: 'appearance',
    label: 'Theme',
    description: 'Light, dark, or follow the system.',
    keywords: ['theme', 'light', 'dark', 'appearance', 'color'],
    control: 'seg',
    options: THEME_OPTS,
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  {
    key: 'appearance.motion',
    category: 'appearance',
    label: 'Motion',
    description: 'Animation level. System honors prefers-reduced-motion.',
    keywords: ['motion', 'animation', 'reduced', 'accessibility'],
    control: 'seg',
    options: MOTION_OPTS,
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  {
    key: 'appearance.density',
    category: 'appearance',
    label: 'Density',
    description: 'Spacing of chrome and controls.',
    keywords: ['density', 'compact', 'comfortable', 'spacing', 'layout'],
    control: 'seg',
    options: DENSITY_OPTS,
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  {
    key: 'appearance.grain',
    category: 'appearance',
    label: 'Film grain',
    description: 'Subtle grain texture over the canvas.',
    keywords: ['grain', 'texture', 'noise', 'canvas', 'effect'],
    control: 'toggle',
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  // ── loopDefaults ──────────────────────────────────────────────────────────
  {
    key: 'loopDefaults.ceilingUsd',
    category: 'loopDefaults',
    label: 'Default cost ceiling',
    description: 'The cost ceiling seeded into a new loop.',
    keywords: ['ceiling', 'cost', 'budget', 'usd', 'spend', 'default'],
    control: 'number',
    min: 0,
    step: 1,
    unit: 'USD',
    apply: 'instant',
    scope: 'both',
    backend: false,
    replaces: 'DEFAULT_CEILING (stores/cosmos.svelte.ts)',
    validate: vCeiling,
  },
  {
    key: 'loopDefaults.runner',
    category: 'loopDefaults',
    label: 'Default runner',
    description: 'The CLI agent a new loop uses.',
    keywords: ['runner', 'agent', 'claude', 'codex', 'aider', 'default'],
    control: 'select',
    options: RUNNER_OPTS,
    apply: 'instant',
    scope: 'both',
    backend: false,
  },
  {
    key: 'loopDefaults.model',
    category: 'loopDefaults',
    label: 'Default model',
    description: 'The model tier seeded into a new loop.',
    keywords: ['model', 'haiku', 'sonnet', 'opus', 'default'],
    control: 'select',
    options: MODEL_OPTS,
    apply: 'instant',
    scope: 'both',
    backend: false,
  },
  {
    key: 'loopDefaults.effort',
    category: 'loopDefaults',
    label: 'Default reasoning effort',
    description: 'The reasoning-effort tier seeded into a new loop.',
    keywords: ['effort', 'reasoning', 'thinking', 'default'],
    control: 'select',
    options: EFFORT_OPTS,
    apply: 'instant',
    scope: 'both',
    backend: false,
  },
  {
    key: 'loopDefaults.permissionMode',
    category: 'loopDefaults',
    label: 'Default permission mode',
    description: 'How much the agent may do without asking, in a new loop.',
    keywords: ['permission', 'accept', 'edits', 'bypass', 'plan', 'default'],
    control: 'select',
    options: PERMISSION_OPTS,
    apply: 'instant',
    scope: 'both',
    backend: false,
  },
  {
    key: 'loopDefaults.blueprint',
    category: 'loopDefaults',
    label: 'Default blueprint',
    description: 'The preset star-chart a new loop starts from.',
    keywords: ['blueprint', 'preset', 'grind', 'sprint', 'explore', 'custom'],
    control: 'select',
    options: BLUEPRINT_OPTS,
    apply: 'instant',
    scope: 'both',
    backend: false,
  },
  // ── notifications ─────────────────────────────────────────────────────────
  {
    key: 'notifications.unattendedAlerts',
    category: 'notifications',
    label: 'Unattended alerts',
    description: 'Raise an alert when a running loop reaches a rest state you care about.',
    keywords: ['alert', 'notify', 'unattended', 'notification'],
    control: 'toggle',
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  {
    key: 'notifications.alertOn',
    category: 'notifications',
    label: 'Alert on',
    description: 'Which rest states trigger an unattended alert.',
    keywords: ['alert', 'rest', 'state', 'quota', 'failed', 'done', 'handoff'],
    control: 'seg',
    options: ALERT_OPTS,
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  {
    key: 'notifications.sound',
    category: 'notifications',
    label: 'Alert sound',
    description: 'Play a sound with an alert.',
    keywords: ['sound', 'audio', 'chime', 'alert'],
    control: 'toggle',
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  {
    key: 'notifications.quotaResumeToast',
    category: 'notifications',
    label: 'Quota resume toast',
    description: 'Show a toast when a loop resumes after a quota wait.',
    keywords: ['quota', 'resume', 'toast', 'notification'],
    control: 'toggle',
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  // ── ai (dynamic instances live outside the registry; this anchors search) ──
  {
    key: 'ai',
    category: 'ai',
    label: 'AI providers & keys (BYOK)',
    description: 'Bring-your-own-key provider instances, default and fallback selection.',
    keywords: ['ai', 'byok', 'provider', 'api key', 'anthropic', 'openai', 'bedrock', 'vertex', 'ollama', 'openrouter', 'gateway', 'keychain'],
    control: 'readonly',
    apply: 'instant',
    scope: 'user',
    backend: true,
  },
  // ── diagnostics ───────────────────────────────────────────────────────────
  {
    key: 'diagnostics.logLevel',
    category: 'diagnostics',
    label: 'Log level',
    description: 'Verbosity of app diagnostics.',
    keywords: ['log', 'level', 'debug', 'info', 'diagnostics', 'verbose'],
    control: 'seg',
    options: LOGLEVEL_OPTS,
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
  {
    key: 'diagnostics.telemetry',
    category: 'diagnostics',
    label: 'Telemetry',
    description: 'No telemetry is collected. This app never phones home.',
    keywords: ['telemetry', 'analytics', 'privacy', 'tracking'],
    control: 'readonly',
    apply: 'instant',
    scope: 'user',
    backend: false,
  },
];

// ─── Registry / defaults helpers ────────────────────────────────────────────

/** All scalar setting metas in a category, in registry order. */
export function settingsForCategory(cat: keyof Settings): SettingMeta[] {
  return REGISTRY.filter((m) => m.category === cat);
}

/** Read a DEFAULTS value by dotted path (e.g. 'general.lanPort'); undefined if absent. */
export function defaultFor(key: string): unknown {
  return key.split('.').reduce<unknown>((acc, part) => {
    if (acc && typeof acc === 'object' && part in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[part];
    }
    return undefined;
  }, DEFAULTS as unknown);
}
