import type { EngineResourceState } from "../hooks/useEngineResource";

export interface RefreshFlags {
  readonly refreshing: boolean;
  readonly refreshError: string | null;
}

export function refreshFlags<T>(state: EngineResourceState<T>): RefreshFlags {
  return state.status === "ready"
    ? { refreshing: state.refreshing, refreshError: state.refreshError }
    : { refreshing: false, refreshError: null };
}

export function RefreshingLabel({ testId }: Readonly<{ testId: string }>) {
  return (
    <span
      role="status"
      aria-live="polite"
      className="shrink-0 whitespace-nowrap text-[10px] font-medium text-text-muted"
      data-testid={testId}
    >
      Refreshing…
    </span>
  );
}

export function RefreshErrorLine({
  testId,
  message,
}: Readonly<{ testId: string; message: string }>) {
  return (
    <p role="status" aria-live="polite" className="text-xs text-text-muted" data-testid={testId}>
      Refresh failed — {message}
    </p>
  );
}
