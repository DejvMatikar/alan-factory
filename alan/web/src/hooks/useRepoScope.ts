import { useEffect, useMemo } from "react";
import { useMatch } from "react-router-dom";

import type { EngineSummary } from "../api";
import { useWorkspaceRepoKey } from "../api/queries";
import { repoSectionKey, switcherEntries, type RepoRegistryEntry } from "../lib/fold";

export const REPO_SCOPE_STORAGE_KEY = "repo-scope";

export interface RepoScope {
  readonly key: string | null;
  readonly entry: RepoRegistryEntry | null;
}

const NO_SCOPE: RepoScope = { key: null, entry: null };

function scopeFromKey(key: string, entries: readonly RepoRegistryEntry[]): RepoScope {
  return { key, entry: entries.find((e) => e.key === key) ?? null };
}

function mirroredScope(entries: readonly RepoRegistryEntry[]): RepoScope {
  const mirrored = sessionStorage.getItem(REPO_SCOPE_STORAGE_KEY);
  const entry = mirrored === null ? null : entries.find((e) => e.key === mirrored) ?? null;
  return entry === null ? NO_SCOPE : { key: entry.key, entry };
}

function workspaceScope(
  engineKey: string | null | undefined,
  entries: readonly RepoRegistryEntry[],
  defaultKey: string | null,
): RepoScope {
  if (engineKey === undefined) return NO_SCOPE;
  const key = repoSectionKey(engineKey, defaultKey);
  const entry = entries.find((e) => e.key === key);
  return entry === undefined ? NO_SCOPE : { key: entry.key, entry };
}

export function useRepoScope(
  engines: readonly EngineSummary[],
  defaultKey: string | null,
): RepoScope {
  const match = useMatch("/repos/:owner/:repo/*");
  const workspaceMatch = useMatch("/workspaces/:taskId/*");
  const workspaceRepoKey = useWorkspaceRepoKey(workspaceMatch?.params.taskId);
  const entries = useMemo(() => switcherEntries(engines, defaultKey), [engines, defaultKey]);
  const owner = match?.params.owner;
  const repo = match?.params.repo;
  const paramsKey = owner !== undefined && repo !== undefined ? `${owner}/${repo}` : null;

  useEffect(() => {
    if (paramsKey === null) return;
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, paramsKey);
  }, [paramsKey]);

  if (paramsKey !== null) return scopeFromKey(paramsKey, entries);
  if (workspaceMatch !== null) return workspaceScope(workspaceRepoKey, entries, defaultKey);
  return mirroredScope(entries);
}
