import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

import { registeredRepoEntries, type RepoRegistryEntry } from "../../../lib/fold";
import { useEngines } from "../../../hooks/useEngines";
import { useRepoScope } from "../../../hooks/useRepoScope";

export interface RepoSelectionState {
  reposLoaded: boolean;
  onlyDefaultRepo: boolean;
  defaultEngineKey: string | null;
  soleRepoKey: string | null;
  showChooser: boolean;
  repos: RepoRegistryEntry[];
  selectedEngineKey: string | undefined;
  selectRepo: (engineKey: string) => void;
}

function rawEngineParam(searchParams: URLSearchParams): string | null {
  const raw = searchParams.get("engine");
  return raw === null || raw === "" ? null : raw;
}

function validatedEngineKey(
  raw: string | null,
  repos: readonly RepoRegistryEntry[],
): string | undefined {
  if (raw === null) return undefined;
  return repos.some((r) => r.key === raw) ? raw : undefined;
}

function resolveEngineKey(
  paramEngineKey: string | undefined,
  paramPresent: boolean,
  scopeKey: string | null,
  isResuming: boolean,
  isProjectScoped: boolean,
): string | undefined {
  if (paramEngineKey !== undefined) return paramEngineKey;
  if (paramPresent || isResuming || isProjectScoped) return undefined;
  return scopeKey ?? undefined;
}

export function useRepoSelection(): RepoSelectionState {
  const { engines, loaded, defaultEngineKey } = useEngines();
  const [searchParams, setSearchParams] = useSearchParams();
  const { key: scopeKey } = useRepoScope(engines, defaultEngineKey);

  const repos = registeredRepoEntries(engines, defaultEngineKey);
  const onlyDefaultRepo = repos.length <= 1;
  const soleRepoKey = repos.length === 1 ? repos[0]!.key : null;
  const rawEngine = rawEngineParam(searchParams);
  const paramEngineKey = validatedEngineKey(rawEngine, repos);
  const isResuming = searchParams.get("s") !== null;
  const isProjectScoped = searchParams.get("project") !== null;
  const selectedEngineKey = resolveEngineKey(
    paramEngineKey,
    rawEngine !== null,
    scopeKey,
    isResuming,
    isProjectScoped,
  );
  const selectionMade = selectedEngineKey !== undefined;

  const selectRepo = useCallback(
    (engineKey: string) => {
      const next = new URLSearchParams(searchParams);
      next.set("engine", engineKey);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  return {
    reposLoaded: loaded,
    onlyDefaultRepo,
    defaultEngineKey,
    soleRepoKey,
    showChooser: loaded && !onlyDefaultRepo && !selectionMade && !isResuming && !isProjectScoped,
    repos,
    selectedEngineKey,
    selectRepo,
  };
}
