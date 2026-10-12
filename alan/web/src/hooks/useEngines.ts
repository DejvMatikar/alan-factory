import { useMemo } from "react";

import type { EngineSummary } from "../api";
import {
  useEnginesListQuery,
  useInvalidateEnginesList,
  useListedDefaultEngineKeyQuery,
} from "../api/queries";
import { useChangeRefetch } from "../api/ServerEventsProvider";

export interface DefaultEngineState {
  readonly key: string | null;
  readonly settled: boolean;
}

export function useDefaultEngineState(): DefaultEngineState {
  const listed = useListedDefaultEngineKeyQuery();
  return { key: listed ?? null, settled: listed !== undefined };
}

export function useDefaultEngineKey(): string | null {
  return useDefaultEngineState().key;
}

export interface UseEnginesResult {
  // Visible only — every chooser, the command palette, and the main view read
  // this field and so never need their own hidden-repo filtering.
  engines: EngineSummary[];
  hiddenEngines: EngineSummary[];
  hiddenCount: number;
  // The "owner/repo" keys of hiddenEngines, as a Set — for a surface (Inbox,
  // HealthView) that filters a list of its own by an item's engine_key rather
  // than by the engine objects themselves.
  hiddenEngineKeys: ReadonlySet<string>;
  // Unfiltered — for the few surfaces (direct-link label resolution) that must
  // keep working for a repo the visible list no longer carries.
  allEngines: EngineSummary[];
  hasNonDefaultEngine: boolean;
  defaultEngineKey: string | null;
  loaded: boolean;
  loadFailed: boolean;
}

const EMPTY_ENGINES: EngineSummary[] = [];

function isHidden(engine: EngineSummary): boolean {
  return engine.hidden === true;
}

function engineKeyOf(engine: EngineSummary): string {
  return `${engine.repo.owner}/${engine.repo.repo}`;
}

export function useEnginesRefresh(): void {
  useChangeRefetch("engines", useInvalidateEnginesList());
}

export function useEngines(): UseEnginesResult {
  const { data, isFetched, isError, errorUpdateCount } = useEnginesListQuery();
  const defaultEngineKey = useDefaultEngineKey();
  const allEngines = data ?? EMPTY_ENGINES;
  const loadFailed = isError || (data === undefined && errorUpdateCount > 0);

  const engines = useMemo(() => allEngines.filter((e) => !isHidden(e)), [allEngines]);
  const hiddenEngines = useMemo(() => allEngines.filter(isHidden), [allEngines]);
  const hiddenEngineKeys = useMemo(
    () => new Set(hiddenEngines.map(engineKeyOf)),
    [hiddenEngines],
  );

  const hasNonDefaultEngine = useMemo(
    () => engines.some((e) => engineKeyOf(e) !== defaultEngineKey),
    [engines, defaultEngineKey],
  );

  return {
    engines,
    hiddenEngines,
    hiddenCount: hiddenEngines.length,
    hiddenEngineKeys,
    allEngines,
    hasNonDefaultEngine,
    defaultEngineKey,
    loaded: isFetched,
    loadFailed,
  };
}
