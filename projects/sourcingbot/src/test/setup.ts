/**
 * Test setup — in-memory localStorage polyfill.
 *
 * Node 22+ defines its own `localStorage` global that stays `undefined` unless
 * the process is started with `--localstorage-file`. Under vitest's jsdom
 * environment `window === globalThis`, so that undefined built-in occupies the
 * slot jsdom would otherwise fill, and both `localStorage` and
 * `window.localStorage` read as undefined.
 *
 * Cue never hit this because its tests never touch storage — its store simply
 * degrades to in-memory via a `typeof localStorage === "undefined"` guard,
 * leaving the persistence path untested. sourcingBOT installs a real in-memory
 * implementation instead, so the store's load/persist round trip is actually
 * exercised rather than silently skipped.
 */
class MemoryStorage implements Storage {
  private data = new Map<string, string>();

  get length(): number {
    return this.data.size;
  }

  clear(): void {
    this.data.clear();
  }

  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null;
  }

  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.data.delete(key);
  }

  setItem(key: string, value: string): void {
    this.data.set(key, String(value));
  }
}

if (typeof globalThis.localStorage === "undefined") {
  Object.defineProperty(globalThis, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
}
