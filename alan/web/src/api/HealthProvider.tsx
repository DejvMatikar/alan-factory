import { createContext, useContext, useMemo } from "react";
import type { ReactNode } from "react";

import type { FinisherRate, HealthResponse, HealthState, QualityMetrics, Sweeper } from "../api";
import { useHealthQuery, useInvalidateHealthContext } from "./queries";
import { useChangeRefetch } from "./ServerEventsProvider";

export type SpendCapStop = NonNullable<HealthResponse["spend_cap_stop"]>;
export type DispatchReadinessIssue = NonNullable<HealthResponse["dispatch_readiness"]>[number];

export interface HealthContextValue {
  queuePaused: boolean;
  pausedUntil: number | null;
  spendCapStop?: SpendCapStop | null;
  dispatchReadiness?: readonly DispatchReadinessIssue[];
  healthState: HealthState | null;
  blockedCount: number;
  needsUserCount: number;
  lastComputedAt: string | null;
  finisherRate: FinisherRate | null;
  qualityMetrics: QualityMetrics | null;
  sweeper: Sweeper | null;
  refetch: () => void;
}

const _noop = () => undefined;

const _defaultValue: HealthContextValue = {
  queuePaused: false,
  pausedUntil: null,
  spendCapStop: null,
  dispatchReadiness: [],
  healthState: null,
  blockedCount: 0,
  needsUserCount: 0,
  lastComputedAt: null,
  finisherRate: null,
  qualityMetrics: null,
  sweeper: null,
  refetch: _noop,
};

export const HealthContext = createContext<HealthContextValue>(_defaultValue);

export function useHealthContext(): HealthContextValue {
  return useContext(HealthContext);
}

function _healthStateCounts(
  health: HealthResponse,
): { healthState: HealthState | null; blockedCount: number; needsUserCount: number } {
  const hs = health.health_state ?? null;
  const blocked = hs === null ? 0 : hs.blocked_count;
  const inFlight = hs === null ? 0 : hs.in_flight_count;
  return { healthState: hs, blockedCount: blocked, needsUserCount: blocked - inFlight };
}

function _scalarNullables(health: HealthResponse) {
  return {
    lastComputedAt: health.last_computed_at ?? null,
    finisherRate: health.finisher_rate ?? null,
    qualityMetrics: health.quality_metrics,
    sweeper: health.sweeper,
  };
}

function _toContextValue(health: HealthResponse): Omit<HealthContextValue, "refetch"> {
  const { healthState, blockedCount, needsUserCount } = _healthStateCounts(health);
  return {
    queuePaused: health.queue_paused,
    pausedUntil: health.paused_until,
    spendCapStop: health.spend_cap_stop ?? null,
    dispatchReadiness: health.dispatch_readiness ?? [],
    healthState,
    blockedCount,
    needsUserCount,
    ..._scalarNullables(health),
  };
}

function _publish(
  data: HealthResponse | undefined,
  isError: boolean,
  refetch: () => void,
): HealthContextValue {
  const value = data === undefined ? _defaultValue : _toContextValue(data);
  return isError
    ? {
        ...value,
        queuePaused: false,
        pausedUntil: null,
        spendCapStop: null,
        dispatchReadiness: [],
        refetch,
      }
    : { ...value, refetch };
}

export function HealthProvider({ children }: Readonly<{ children: ReactNode }>) {
  const { data, isError } = useHealthQuery();
  const invalidate = useInvalidateHealthContext();

  useChangeRefetch("health", invalidate);

  const contextValue = useMemo(
    () => _publish(data, isError, invalidate),
    [data, isError, invalidate],
  );

  return (
    <HealthContext.Provider value={contextValue}>
      {children}
    </HealthContext.Provider>
  );
}
