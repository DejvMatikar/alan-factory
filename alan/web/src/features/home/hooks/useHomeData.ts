import { useCallback, useEffect, useRef, useState } from "react";

import { api, type ChangeKind, type ProjectListEntry, type WorkspaceSummary } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import { useChangeRefetch } from "../../../api/ServerEventsProvider";
import type { Tab } from "../components/tabs";

function sortByCreated(workspaces: readonly WorkspaceSummary[]): WorkspaceSummary[] {
  return [...workspaces].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
}

interface PolledResource<T> {
  items: T[] | null;
  reload: () => void;
}

function usePolledResource<T>(
  fetchList: () => Promise<T[]>,
  kind: ChangeKind,
  enabled: boolean,
  onError: (message: string | null) => void,
): PolledResource<T> {
  const [data, setData] = useState<T[] | null>(null);
  const mountedRef = useRef(true);
  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );
  const load = useCallback(() => {
    fetchList().then(
      (items) => {
        if (!mountedRef.current) return;
        setData(items);
        onError(null);
      },
      (err: unknown) => {
        if (mountedRef.current) onError(errorMessageOf(err));
      },
    );
  }, [fetchList, onError]);
  useChangeRefetch(kind, load, enabled);
  return { items: data, reload: load };
}

function fetchDefaultWorkspaces(): Promise<WorkspaceSummary[]> {
  return api.listWorkspaces().then(sortByCreated);
}

function fetchArchivedWorkspaces(): Promise<WorkspaceSummary[]> {
  return api.listWorkspaces({ bucket: "archived" }).then(sortByCreated);
}

function fetchProjects(): Promise<ProjectListEntry[]> {
  return api.listProjects({ includeArchived: true });
}

export interface UseHomeDataResult {
  readonly workspaces: readonly WorkspaceSummary[] | null;
  readonly liveWorkspaces: readonly WorkspaceSummary[] | null;
  readonly projects: readonly ProjectListEntry[] | null;
  readonly loadError: string | null;
  readonly reloadWorkspaces: () => void;
}

export function useHomeData(tab: Tab): UseHomeDataResult {
  const [wsLoadError, setWsLoadError] = useState<string | null>(null);
  const [projectsLoadError, setProjectsLoadError] = useState<string | null>(null);

  const live = usePolledResource(fetchDefaultWorkspaces, "workspace_list", tab !== "archived", setWsLoadError);
  const archived = usePolledResource(
    fetchArchivedWorkspaces,
    "workspace_list",
    tab === "archived",
    setWsLoadError,
  );
  const projects = usePolledResource(fetchProjects, "workspace_list", true, setProjectsLoadError);
  const { items: workspaces, reload: reloadWorkspaces } = tab === "archived" ? archived : live;
  const loadError = wsLoadError ?? projectsLoadError;

  return {
    workspaces,
    liveWorkspaces: live.items,
    projects: projects.items,
    loadError,
    reloadWorkspaces,
  };
}
