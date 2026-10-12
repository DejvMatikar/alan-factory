import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import type { ProjectListEntry, WorkspaceSummary } from "../../../api";
import { HIDDEN_REPO_DISCLOSURE_SUMMARY_ID } from "../../../components/HiddenRepoDialog";
import { resolveAccents } from "../../../lib/repoAccent";
import { useEngines } from "../../../hooks/useEngines";
import { useProjectFeatures } from "../../../hooks/useProjectFeatures";
import {
  buildRepoSections,
  registeredRepoEntries,
  repoHeaderModel,
  titleCaseRepoName,
  type RepoSectionModel,
} from "../../../lib/fold";
import RepoPageBody from "../components/RepoPageBody";
import type { Tab } from "../components/tabs";
import { useConnectStates } from "../hooks/useConnectStates";
import { useHomeData } from "../hooks/useHomeData";

const TAB_SESSION_KEY = "workspace-list-tab";

function readStoredTab(): Tab {
  const stored = sessionStorage.getItem(TAB_SESSION_KEY);
  return stored === "archived" ? "archived" : "active";
}

function emptySectionFor(key: string): RepoSectionModel {
  return {
    key,
    title: titleCaseRepoName(key),
    posture: null,
    projectGroups: [],
    unassigned: [],
    shippedCount: 0,
    archivedCount: 0,
  };
}

function scopedSectionFor(
  engines: Parameters<typeof registeredRepoEntries>[0],
  projects: readonly ProjectListEntry[] | null,
  workspaces: readonly WorkspaceSummary[] | null,
  showProjects: boolean,
  scopedKey: string,
  defaultKey: string | null,
): RepoSectionModel {
  if (projects === null) return emptySectionFor(scopedKey);
  const sections = buildRepoSections(engines, projects, workspaces ?? [], showProjects, defaultKey);
  return sections.find((s) => s.key === scopedKey) ?? emptySectionFor(scopedKey);
}

function useRedirectWhenScopeDisappears(
  scopedKey: string | null,
  engines: Parameters<typeof registeredRepoEntries>[0],
  loaded: boolean,
  loadFailed: boolean,
  defaultKey: string | null,
): void {
  const navigate = useNavigate();
  useEffect(() => {
    if (scopedKey === null || !loaded || loadFailed) return;
    const visibleKeys = new Set(registeredRepoEntries(engines, defaultKey).map((entry) => entry.key));
    if (visibleKeys.has(scopedKey)) return;
    void navigate("/", { replace: true });
    if (document.activeElement === document.body) {
      document.getElementById(HIDDEN_REPO_DISCLOSURE_SUMMARY_ID)?.focus();
    }
  }, [scopedKey, engines, loaded, loadFailed, defaultKey, navigate]);
}

export default function Repo() {
  const { owner, repo } = useParams<{ owner: string; repo: string }>();
  const scopedKey = owner !== undefined && repo !== undefined ? `${owner}/${repo}` : null;
  const { engines, allEngines, loaded, loadFailed, defaultEngineKey } = useEngines();
  const showProjects = useProjectFeatures();

  const [tab, setTab] = useState<Tab>(readStoredTab);

  const { workspaces, projects, loadError, reloadWorkspaces } = useHomeData(tab);
  const connectStates = useConnectStates({ engines, workspaces, projects, defaultEngineKey });

  useRedirectWhenScopeDisappears(scopedKey, engines, loaded, loadFailed, defaultEngineKey);

  const selectTab = useCallback((t: Tab): void => {
    sessionStorage.setItem(TAB_SESSION_KEY, t);
    setTab(t);
  }, []);

  if (scopedKey === null) return null;

  const header = repoHeaderModel(allEngines, scopedKey, defaultEngineKey);
  const ready = workspaces !== null && projects !== null;
  const section = scopedSectionFor(
    engines,
    projects,
    workspaces,
    showProjects,
    scopedKey,
    defaultEngineKey,
  );
  const resolvedAccent = resolveAccents(allEngines).get(scopedKey) ?? null;

  return (
    <RepoPageBody
      tab={tab}
      onSelectTab={selectTab}
      loadError={loadError}
      ready={ready}
      header={header}
      section={section}
      reload={reloadWorkspaces}
      showProjects={showProjects}
      resolvedAccent={resolvedAccent}
      postureResolved={loaded}
      hostSetupMessage={connectStates.hostSetupMessage}
      resumes={connectStates.resumes}
    />
  );
}
