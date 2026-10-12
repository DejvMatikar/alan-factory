import { useCallback, useState } from "react";

import { api } from "../../../api";
import type { DriverLoopStatus } from "../../../api";
import { useChangeRefetch } from "../../../api/ServerEventsProvider";

export interface UseDriverLoopsResult {
  loops: DriverLoopStatus[];
  supervised: boolean;
  loaded: boolean;
  error: boolean;
}

const _EMPTY_LOOPS: DriverLoopStatus[] = [];

export function useDriverLoops(): UseDriverLoopsResult {
  const [loops, setLoops] = useState<DriverLoopStatus[]>(_EMPTY_LOOPS);
  const [supervised, setSupervised] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const fetchLoops = useCallback(() => {
    api.getDriverLoops().then(
      (resp) => {
        setLoops(resp.loops);
        setSupervised(resp.supervised);
        setLoaded(true);
        setError(false);
      },
      () => {
        setLoaded(true);
        setError(true);
      },
    );
  }, []);

  useChangeRefetch("health", fetchLoops);

  return { loops, supervised, loaded, error };
}
