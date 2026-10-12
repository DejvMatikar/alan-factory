import type {
  EnginePosture,
  EngineSummary,
  ProjectListEntry,
  UnblockRecord,
  WorkspaceStatus,
  WorkspaceSummary,
} from "../api";
import { isHumanOwnedPark } from "./unblockView";

export type AggregateState = "needs_you" | "executing" | "idle";

export const NEEDS_YOU_STATUSES: ReadonlySet<WorkspaceStatus> = new Set([
  "awaiting_input",
  "escalated",
]);

export interface AggregateStatusEntry {
  readonly status: WorkspaceStatus;
  readonly unblock?: UnblockRecord | null;
}

export function aggregateStateForStatuses(
  entries: readonly AggregateStatusEntry[],
  nowMs: number = Date.now(),
): AggregateState {
  const needsYou = entries.some(
    (entry) => NEEDS_YOU_STATUSES.has(entry.status) && isHumanOwnedPark(entry.unblock, nowMs),
  );
  if (needsYou) return "needs_you";
  if (entries.some((entry) => entry.status === "active")) return "executing";
  return "idle";
}

export function repoSectionKey(
  engineKey: string | null | undefined,
  defaultKey: string | null,
): string | null {
  return engineKey ?? defaultKey;
}

export function engineKeyMatchesRepo(
  itemEngineKey: string | null | undefined,
  repoKey: string,
  defaultKey: string | null,
): boolean {
  return repoSectionKey(itemEngineKey, defaultKey) === repoKey;
}

function titleCaseWord(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

export function titleCaseRepoName(slug: string): string {
  const repoPart = slug.includes("/") ? slug.slice(slug.indexOf("/") + 1) : slug;
  return repoPart
    .split(/[-_]+/)
    .filter((word) => word.length > 0)
    .map(titleCaseWord)
    .join(" ");
}

export interface RepoRegistryEntry {
  readonly key: string;
  readonly title: string;
  readonly posture: EnginePosture | null;
}

export type RepoHeaderModel = RepoRegistryEntry;

export function repoHeaderModel(
  engines: readonly EngineSummary[],
  repoKey: string,
  defaultKey: string | null,
): RepoHeaderModel {
  const entries = registeredRepoEntries(engines, defaultKey);
  return entries.find((entry) => entry.key === repoKey) ?? {
    key: repoKey,
    title: titleCaseRepoName(repoKey),
    posture: null,
  };
}

export function compareRepoEntries(
  defaultKey: string | null,
): (a: RepoRegistryEntry, b: RepoRegistryEntry) => number {
  return (a, b) => {
    if (a.key === b.key) return 0;
    if (a.key === defaultKey) return -1;
    if (b.key === defaultKey) return 1;
    return a.title.localeCompare(b.title);
  };
}

export function registeredRepoEntries(
  engines: readonly EngineSummary[],
  defaultKey: string | null,
): RepoRegistryEntry[] {
  const seen = new Map<string, RepoRegistryEntry>();
  for (const engine of engines) {
    const key = `${engine.repo.owner}/${engine.repo.repo}`;
    if (!seen.has(key)) {
      seen.set(key, { key, title: titleCaseRepoName(key), posture: engine.posture });
    }
  }
  if (defaultKey !== null && !seen.has(defaultKey)) {
    seen.set(defaultKey, {
      key: defaultKey,
      title: titleCaseRepoName(defaultKey),
      posture: null,
    });
  }
  return Array.from(seen.values()).sort(compareRepoEntries(defaultKey));
}

function listsSwitchableRepository(
  engines: readonly EngineSummary[],
  defaultKey: string | null,
): boolean {
  return engines.some(
    (engine) => `${engine.repo.owner}/${engine.repo.repo}` !== defaultKey || engine.forge != null,
  );
}

export function switcherEntries(
  engines: readonly EngineSummary[],
  defaultKey: string | null,
): RepoRegistryEntry[] {
  if (!listsSwitchableRepository(engines, defaultKey)) return [];
  const postureByKey = new Map<string, EnginePosture | null>();
  for (const engine of engines) {
    const key = `${engine.repo.owner}/${engine.repo.repo}`;
    if (!postureByKey.has(key)) postureByKey.set(key, engine.posture);
  }

  const shortNameCounts = new Map<string, number>();
  for (const key of postureByKey.keys()) {
    const shortName = titleCaseRepoName(key);
    shortNameCounts.set(shortName, (shortNameCounts.get(shortName) ?? 0) + 1);
  }

  const entries: RepoRegistryEntry[] = Array.from(postureByKey.entries()).map(([key, posture]) => {
    const shortName = titleCaseRepoName(key);
    /* v8 ignore next */
    const title = (shortNameCounts.get(shortName) ?? 0) > 1 ? key : shortName;
    return { key, title, posture };
  });
  return entries.sort(compareRepoEntries(defaultKey));
}

export interface ProjectGroup {
  readonly project: ProjectListEntry;
  readonly workspaces: readonly WorkspaceSummary[];
}

export interface RepoSectionModel {
  readonly key: string;
  readonly title: string;
  readonly posture: EnginePosture | null;
  readonly projectGroups: readonly ProjectGroup[];
  readonly unassigned: readonly WorkspaceSummary[];
  readonly shippedCount: number;
  readonly archivedCount: number;
}

const TERMINAL_PROJECT_STATUSES: ReadonlySet<ProjectListEntry["status"]> = new Set([
  "completed",
  "archived",
]);

export const UNPLACED_SECTION_KEY = "unplaced";
export const UNPLACED_SECTION_TITLE = "No repository yet";

function projectRepoKey(project: ProjectListEntry, defaultKey: string | null): string | null {
  return repoSectionKey(project.engine_key ?? null, defaultKey);
}

export function projectPlacementKey(
  project: ProjectListEntry,
  sectionKeys: ReadonlySet<string>,
): string {
  if (project.engine_key != null) return project.engine_key;
  const intended = project.intended_engine_key ?? null;
  if (intended != null && sectionKeys.has(intended)) return intended;
  return UNPLACED_SECTION_KEY;
}

function workspaceRepoKey(ws: WorkspaceSummary, defaultKey: string | null): string | null {
  return repoSectionKey(ws.engine_key ?? null, defaultKey);
}

function groupWorkspacesByProject(
  workspaces: readonly WorkspaceSummary[],
): Map<string, WorkspaceSummary[]> {
  const byProject = new Map<string, WorkspaceSummary[]>();
  for (const ws of workspaces) {
    if (ws.project_id == null) continue;
    const list = byProject.get(ws.project_id);
    if (list) list.push(ws);
    else byProject.set(ws.project_id, [ws]);
  }
  return byProject;
}

export function liveProjectIdSet(projects: readonly ProjectListEntry[]): ReadonlySet<string> {
  return new Set(
    projects.filter((p) => !TERMINAL_PROJECT_STATUSES.has(p.status)).map((p) => p.id),
  );
}

function isOrphanedFromLiveProject(
  ws: WorkspaceSummary,
  liveProjectIds: ReadonlySet<string>,
): boolean {
  return ws.project_id == null || !liveProjectIds.has(ws.project_id);
}

interface RepoSectionContext {
  readonly projects: readonly ProjectListEntry[];
  readonly workspacesByProject: ReadonlyMap<string, WorkspaceSummary[]>;
  readonly workspaces: readonly WorkspaceSummary[];
  readonly liveProjectIds: ReadonlySet<string>;
  readonly showProjects: boolean;
  readonly placementKey: (project: ProjectListEntry) => string | null;
  readonly defaultKey: string | null;
}

function buildRepoSection(entry: RepoRegistryEntry, context: RepoSectionContext): RepoSectionModel {
  const { projects, workspacesByProject, workspaces, liveProjectIds, showProjects, placementKey, defaultKey } =
    context;
  const repoProjects = projects.filter((p) => placementKey(p) === entry.key);
  const liveProjects = repoProjects.filter((p) => liveProjectIds.has(p.id));
  const projectGroups = showProjects
    ? liveProjects.map((project) => ({
        project,
        workspaces: workspacesByProject.get(project.id) ?? [],
      }))
    : [];
  const unassigned = workspaces.filter(
    (ws) =>
      workspaceRepoKey(ws, defaultKey) === entry.key &&
      (showProjects ? isOrphanedFromLiveProject(ws, liveProjectIds) : true),
  );
  return {
    key: entry.key,
    title: entry.title,
    posture: entry.posture,
    projectGroups,
    unassigned,
    shippedCount: repoProjects.filter((p) => p.status === "completed").length,
    archivedCount: repoProjects.filter((p) => p.status === "archived").length,
  };
}

export function buildRepoSections(
  engines: readonly EngineSummary[],
  projects: readonly ProjectListEntry[],
  workspaces: readonly WorkspaceSummary[],
  showProjects: boolean,
  defaultKey: string | null,
): RepoSectionModel[] {
  const workspacesByProject = groupWorkspacesByProject(workspaces);
  const liveProjectIds = liveProjectIdSet(projects);
  const entries = registeredRepoEntries(engines, defaultKey);
  const sectionKeys: ReadonlySet<string> = new Set(entries.map((entry) => entry.key));
  const placementKey = showProjects
    ? (project: ProjectListEntry) => projectPlacementKey(project, sectionKeys)
    : (project: ProjectListEntry) => projectRepoKey(project, defaultKey);
  const context: RepoSectionContext = {
    projects,
    workspacesByProject,
    workspaces,
    liveProjectIds,
    showProjects,
    placementKey,
    defaultKey,
  };
  return entries.map((entry) => buildRepoSection(entry, context));
}

export function isRepoSectionEmpty(section: RepoSectionModel): boolean {
  return section.projectGroups.length === 0 && section.unassigned.length === 0;
}

export function buildUnplacedProjectSection(
  projects: readonly ProjectListEntry[],
  liveProjectIds: ReadonlySet<string>,
  showProjects: boolean,
  sectionKeys: ReadonlySet<string>,
): RepoSectionModel | null {
  if (!showProjects) return null;
  const unplacedProjects = projects.filter(
    (p) => projectPlacementKey(p, sectionKeys) === UNPLACED_SECTION_KEY,
  );
  const liveProjects = unplacedProjects.filter((p) => liveProjectIds.has(p.id));
  const projectGroups: ProjectGroup[] = liveProjects.map((project) => ({
    project,
    workspaces: [],
  }));
  return {
    key: UNPLACED_SECTION_KEY,
    title: UNPLACED_SECTION_TITLE,
    posture: null,
    projectGroups,
    unassigned: [],
    shippedCount: unplacedProjects.filter((p) => p.status === "completed").length,
    archivedCount: unplacedProjects.filter((p) => p.status === "archived").length,
  };
}

export function sumActiveSeconds(workspaces: readonly WorkspaceSummary[]): number | null {
  const recorded = workspaces
    .map((ws) => ws.active_seconds)
    .filter((seconds): seconds is number => seconds != null);
  if (recorded.length === 0) return null;
  return recorded.reduce((sum, seconds) => sum + seconds, 0);
}
