/**
 * Pure connection-state derivation. Kept out of the store so the LIVE /
 * DEGRADED / DEMO logic is unit-testable and the badge stays declarative.
 */

import type { DataMode } from "@/adapter";

export type Connection = "live" | "degraded" | "demo" | "connecting";

export function deriveConnection(mode: DataMode | "loading", healthOk: boolean): Connection {
  if (mode === "loading") return "connecting";
  if (mode === "demo") return "demo";
  return healthOk ? "live" : "degraded";
}
