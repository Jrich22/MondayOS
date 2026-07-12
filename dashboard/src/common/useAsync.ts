import { useEffect, useState } from "react";
import type { ActionResult } from "@/adapter/types";

/**
 * Run an adapter read and expose {loading, data, error}. Keeps async + loading
 * state out of the workspace components and gives every panel a consistent
 * loading / error surface. Re-runs when `deps` change.
 */
export interface AsyncState<T> {
  loading: boolean;
  data?: T;
  error?: { code: string; message: string };
}

export function useAsync<T>(
  run: () => Promise<ActionResult<T>>,
  deps: unknown[],
  enabled = true,
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ loading: true });
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setState({ loading: true });
    run().then((r) => {
      if (cancelled) return;
      if (r.ok) setState({ loading: false, data: r.data });
      else setState({ loading: false, error: r.error });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled]);
  return state;
}
