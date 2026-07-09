// settings/keychain.ts — thin client over the OS-keychain + BYOK auth Rust
// commands (WS-A). Secrets are WRITE-ONLY from the UI: `keychainSet` takes a
// secret and returns nothing; there is deliberately NO getter — the secret only
// ever leaves the keychain inside the Rust `byok_env()` at spawn time. The UI
// learns "is a secret present?" via `keychainHas`, never its value.
//
// In dev (no Tauri) every call no-ops gracefully: sets/deletes are silent,
// presence probes return `false`, auth probes return `null`.

import { CMD, type ByokAuthStatus } from './contract';
import { providerRule, type ProviderInstance } from './schema';
import { hasTauri } from './backend';

async function invokeCmd<T>(cmd: string, args: Record<string, unknown>): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke(cmd, args) as Promise<T>;
}

/**
 * The OS-keychain account a given instance's secret lives under, or `null` when
 * the (provider, runner, mode) triple needs no secret (e.g. a subscription).
 * Account == the matrix rule's `keychainAccount` (kept simple: one account per
 * provider/mode, shared across instances of the same credential profile).
 */
export function accountFor(inst: Pick<ProviderInstance, 'provider' | 'runner' | 'mode'>): string | null {
  return providerRule(inst.provider, inst.runner, inst.mode).keychainAccount;
}

/** Store (or overwrite) a secret under `account`. No-op without Tauri. */
export async function keychainSet(account: string, secret: string): Promise<void> {
  if (!hasTauri()) return;
  await invokeCmd(CMD.keychainSet, { account, secret });
}

/** Does the keychain hold a secret for `account`? `false` without Tauri / on error. */
export async function keychainHas(account: string): Promise<boolean> {
  if (!hasTauri()) return false;
  try {
    return await invokeCmd<boolean>(CMD.keychainHas, { account });
  } catch {
    return false;
  }
}

/** Delete the secret under `account`. No-op without Tauri; never throws. */
export async function keychainDelete(account: string): Promise<void> {
  if (!hasTauri()) return;
  try {
    await invokeCmd(CMD.keychainDelete, { account });
  } catch {
    /* nothing to clean up / already gone */
  }
}

/** Probe BYOK auth reachability for one instance. `null` without Tauri / on error. */
export async function byokAuthStatus(inst: ProviderInstance): Promise<ByokAuthStatus | null> {
  if (!hasTauri()) return null;
  try {
    return await invokeCmd<ByokAuthStatus>(CMD.byokAuthStatus, {
      runner: inst.runner,
      provider: inst.provider,
      mode: inst.mode,
      baseUrl: inst.baseUrl,
      region: inst.region,
    });
  } catch {
    return null;
  }
}
