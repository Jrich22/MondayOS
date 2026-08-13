/**
 * Deterministic-ish id helpers.
 *
 * No crypto dependency and no uuid package: the foundation increment persists
 * to localStorage only, so ids need to be unique within a browser profile, not
 * globally. A monotonic counter is mixed into the seed so two ids minted in the
 * same millisecond cannot collide — the failure mode a bare Date.now() has.
 */

let counter = 0;

export function newId(prefix: string): string {
  counter += 1;
  const stamp = Date.now().toString(36);
  const seq = counter.toString(36).padStart(3, "0");
  const salt = Math.floor(Math.random() * 36 ** 3)
    .toString(36)
    .padStart(3, "0");
  return `${prefix}_${stamp}${seq}${salt}`;
}

/** Reset the counter. Test-only seam. */
export function __resetIdCounter(): void {
  counter = 0;
}

export function nowIso(): string {
  return new Date().toISOString();
}
