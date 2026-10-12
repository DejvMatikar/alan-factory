import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

import { api, CHANGE_KINDS, type ChangeKind } from "../api";

export interface ServerEventsValue {
  counters: Record<ChangeKind, number>;
  connected: boolean;
}

const _ZERO_COUNTERS: Record<ChangeKind, number> = {
  workspace_list: 0,
  health: 0,
  engines: 0,
};

const _defaultValue: ServerEventsValue = {
  counters: _ZERO_COUNTERS,
  connected: false,
};

export const ServerEventsContext = createContext<ServerEventsValue>(_defaultValue);

export function useServerEvents(): ServerEventsValue {
  return useContext(ServerEventsContext);
}

const _BACKOFF_BASE_MS = 1000;
const _BACKOFF_MAX_MS = 30000;
export const FALLBACK_POLL_MS = 15000;

function jitterFraction(): number {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return buf.reduce((acc, value) => acc + value, 0) / 0xffffffff;
}

function backoffDelay(attempt: number): number {
  const base = Math.min(_BACKOFF_BASE_MS * 2 ** attempt, _BACKOFF_MAX_MS);
  return base * (0.75 + jitterFraction() * 0.5);
}

export function ServerEventsProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [counters, setCounters] = useState<Record<ChangeKind, number>>(_ZERO_COUNTERS);
  const [connected, setConnected] = useState(false);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);

  const bump = useCallback((kinds: readonly ChangeKind[]) => {
    setCounters((prev) => {
      const next = { ...prev };
      for (const kind of kinds) next[kind] += 1;
      return next;
    });
  }, []);

  const teardown = useCallback(() => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = null;
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }, []);

  const connect = useCallback(
    function connectFn(): void {
      if (document.hidden) return;
      unsubscribeRef.current?.();
      unsubscribeRef.current = api.subscribeEvents({
        onChanged: (kinds) => {
          attemptRef.current = 0;
          bump(kinds);
        },
        onDisconnect: () => {
          setConnected(false);
          const delay = backoffDelay(attemptRef.current);
          attemptRef.current += 1;
          timerRef.current = setTimeout(connectFn, delay);
        },
      });
      setConnected(true);
      bump(CHANGE_KINDS);
    },
    [bump],
  );

  useEffect(() => {
    const onVisibility = (): void => {
      if (document.hidden) {
        teardown();
        setConnected(false);
      } else {
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    connect();
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      teardown();
    };
  }, [connect, teardown]);

  const value = useMemo(() => ({ counters, connected }), [counters, connected]);
  return (
    <ServerEventsContext.Provider value={value}>{children}</ServerEventsContext.Provider>
  );
}

export function useChangeRefetch(
  kind: ChangeKind,
  refetch: () => void,
  enabled = true,
): void {
  const { counters, connected } = useServerEvents();
  const count = counters[kind];
  const lastRefetchedRef = useRef<number | null>(null);
  useEffect(() => {
    if (!enabled) {
      lastRefetchedRef.current = null;
      return undefined;
    }
    if (lastRefetchedRef.current !== count && !document.hidden) {
      lastRefetchedRef.current = count;
      refetch();
    }
    return undefined;
  }, [enabled, refetch, count]);
  useEffect(() => {
    if (!enabled || connected) return undefined;
    const id = setInterval(() => {
      if (!document.hidden) refetch();
    }, FALLBACK_POLL_MS);
    return () => {
      clearInterval(id);
    };
  }, [enabled, connected, refetch]);
}
