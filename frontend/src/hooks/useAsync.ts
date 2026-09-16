import { useCallback, useEffect, useRef, useState } from "react";

interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/** Small data hook: runs `load` on mount and whenever `deps` change,
 * ignores results from superseded calls, and exposes `reload`. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> & {
  reload: () => void;
} {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  const latest = useRef(0);

  useEffect(() => {
    const call = ++latest.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    load().then(
      (data) => {
        if (call === latest.current) setState({ data, error: null, loading: false });
      },
      (err: unknown) => {
        if (call === latest.current)
          setState((s) => ({
            ...s,
            error: err instanceof Error ? err.message : "שגיאה לא צפויה",
            loading: false,
          }));
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}
