/**
 * Action log for the adapter boundary. Every write attempt (and its structured
 * result) is recorded here so the dashboard has an auditable trail independent
 * of MondayOS's own logging. In-memory for Phase 1; a real adapter may also
 * forward these to MondayOS. Deliberately tiny and side-effect-free beyond the
 * ring buffer so it's safe to call from anywhere below the UI.
 */

export interface ActionLogEntry {
  seq: number;
  mode: "live" | "demo";
  op: string;
  args: unknown;
  ok: boolean;
  message?: string;
}

const MAX = 200;
const entries: ActionLogEntry[] = [];
let seq = 0;

export function logAction(entry: Omit<ActionLogEntry, "seq">): ActionLogEntry {
  const full: ActionLogEntry = { seq: ++seq, ...entry };
  entries.push(full);
  if (entries.length > MAX) entries.shift();
  return full;
}

/** Newest-first snapshot of the log. */
export function getActionLog(): ActionLogEntry[] {
  return [...entries].reverse();
}

/** Test/reset helper. */
export function clearActionLog(): void {
  entries.length = 0;
  seq = 0;
}
