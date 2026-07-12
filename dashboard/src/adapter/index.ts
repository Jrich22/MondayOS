/**
 * Adapter selection. The app calls `selectAdapter()` once at startup: if a
 * MondayOS API is reachable it uses the live adapter, otherwise it falls back to
 * the demo adapter — and always reports which, so the UI can show the DEMO DATA
 * badge and never present demo values as live.
 *
 * The MondayOS API base URL comes from `VITE_MONDAYOS_API` when set; with no
 * env and no reachable server (Phase 1), selection resolves to demo.
 */

import type { MondayAdapter } from "./types";
import { createDemoAdapter } from "./demoAdapter";
import { createRealAdapter, probe, type RealAdapterConfig } from "./realAdapter";

export * from "./types";
export { createDemoAdapter } from "./demoAdapter";
export { createRealAdapter, probe } from "./realAdapter";
export { getActionLog, clearActionLog } from "./log";

export interface AdapterSelection {
  adapter: MondayAdapter;
  mode: "live" | "demo";
  /** Base URL of the live API (present only in live mode) — for SSE / probes. */
  baseUrl?: string;
  /** Present when live was attempted but unavailable — surfaced for diagnostics. */
  reason?: string;
}

/** Extra options for selection (e.g. a health callback for the connection badge). */
export interface SelectOptions {
  onHealth?: (ok: boolean) => void;
}

function apiBaseUrl(): string | undefined {
  // Vite exposes env under import.meta.env; guard for non-Vite (test) contexts.
  const env = (import.meta as unknown as { env?: Record<string, string> }).env;
  return env?.VITE_MONDAYOS_API;
}

/**
 * Choose the adapter. Pass an explicit config to force a probe (used in tests);
 * otherwise it reads the Vite env. Never throws — worst case is demo.
 */
export async function selectAdapter(
  cfg?: RealAdapterConfig,
  opts?: SelectOptions,
): Promise<AdapterSelection> {
  const baseUrl = cfg?.baseUrl ?? apiBaseUrl();
  if (baseUrl) {
    const config: RealAdapterConfig = {
      baseUrl,
      fetchImpl: cfg?.fetchImpl,
      onHealth: opts?.onHealth,
    };
    const available = await probe(config);
    if (available) return { adapter: createRealAdapter(config), mode: "live", baseUrl };
    return {
      adapter: createDemoAdapter(),
      mode: "demo",
      reason: `MondayOS API at ${baseUrl} is unreachable — using demo data.`,
    };
  }
  return {
    adapter: createDemoAdapter(),
    mode: "demo",
    reason: "No MondayOS API configured (VITE_MONDAYOS_API) — using demo data.",
  };
}
