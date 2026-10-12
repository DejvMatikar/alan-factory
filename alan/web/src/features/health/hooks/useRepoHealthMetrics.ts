import { useCallback, useState } from "react";

import { api } from "../../../api";
import type { HealthScopeWindows, RepoHealthRow } from "../../../api";
import { useChangeRefetch } from "../../../api/ServerEventsProvider";

export interface UseRepoHealthMetricsResult {
  rows: RepoHealthRow[];
  fleet: HealthScopeWindows | null;
  computedAt: string | null;
  metricsComputedAt: string | null;
  unattributed: number;
  loaded: boolean;
  error: boolean;
}

const _EMPTY_ROWS: RepoHealthRow[] = [];

export function useRepoHealthMetrics(): UseRepoHealthMetricsResult {
  const [rows, setRows] = useState<RepoHealthRow[]>(_EMPTY_ROWS);
  const [fleet, setFleet] = useState<HealthScopeWindows | null>(null);
  const [computedAt, setComputedAt] = useState<string | null>(null);
  const [metricsComputedAt, setMetricsComputedAt] = useState<string | null>(null);
  const [unattributed, setUnattributed] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const fetchRepos = useCallback(() => {
    api.getHealthRepos().then(
      (resp) => {
        setRows(resp.rows);
        setFleet(resp.fleet);
        setComputedAt(resp.computed_at);
        setMetricsComputedAt(resp.metrics_computed_at);
        setUnattributed(resp.unattributed);
        setLoaded(true);
        setError(false);
      },
      () => {
        setLoaded(true);
        setError(true);
      },
    );
  }, []);

  useChangeRefetch("health", fetchRepos);

  return { rows, fleet, computedAt, metricsComputedAt, unattributed, loaded, error };
}
