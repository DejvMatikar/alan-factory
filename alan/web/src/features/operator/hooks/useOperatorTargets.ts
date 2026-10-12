import type { EngineSummary, WorkspaceSummary } from "../../../api";
import type { OperatorScope } from "../../../api/operator";
import { useOperatorWorkspaceTargetsQuery } from "../../../api/queries";
import { STATUS_LABELS } from "../../../components/StatusBadge";
import { useEngines } from "../../../hooks/useEngines";
import { compareRepoEntries, titleCaseRepoName, type RepoRegistryEntry } from "../../../lib/fold";
import type { TargetScope } from "../operatorCopy";
import { WORKSPACE_TARGET_PATTERN } from "../operatorProtocol";

export type TargetStatus = "loading" | "failed" | "empty" | "ready";

export interface TargetOption {
  readonly value: string;
  readonly label: string;
}

interface TargetListing {
  readonly status: TargetStatus;
  readonly options: readonly TargetOption[];
}

export interface OperatorTargets extends TargetListing {
  readonly initial: string;
}

const NO_TARGETS: OperatorTargets = { status: "ready", options: [], initial: "" };

function statusOf(
  loaded: boolean,
  failed: boolean,
  options: readonly TargetOption[],
): TargetStatus {
  if (failed) return "failed";
  if (!loaded) return "loading";
  return options.length === 0 ? "empty" : "ready";
}

function repoOptions(engines: readonly EngineSummary[], defaultKey: string | null): TargetOption[] {
  const entries = new Map<string, RepoRegistryEntry>();
  for (const engine of engines) {
    const key = `${engine.repo.owner}/${engine.repo.repo}`;
    if (!entries.has(key)) {
      entries.set(key, { key, title: titleCaseRepoName(key), posture: engine.posture });
    }
  }
  return [...entries.values()]
    .sort(compareRepoEntries(defaultKey))
    .map((entry) => ({ value: entry.key, label: entry.key }));
}

function workspaceLabel(row: WorkspaceSummary): string {
  const repository = row.engine_key ?? "";
  return [row.title, STATUS_LABELS[row.status], repository].filter((part) => part !== "").join(" · ");
}

function workspaceOptions(rows: readonly WorkspaceSummary[], preselected: string): TargetOption[] {
  const options = rows
    .filter((row) => row.operator_link || row.task_id === preselected)
    .map((row) => ({ value: row.task_id, label: workspaceLabel(row) }));
  const pinned =
    !options.some((option) => option.value === preselected) &&
    WORKSPACE_TARGET_PATTERN.test(preselected);
  return pinned ? [{ value: preselected, label: preselected }, ...options] : options;
}

function initialValue(
  scope: TargetScope,
  options: readonly TargetOption[],
  preselected: string,
): string {
  if (options.some((option) => option.value === preselected)) return preselected;
  return scope === "repo" && options.length === 1 ? options[0]!.value : "";
}

function useRepoListing(): TargetListing {
  const { engines, defaultEngineKey, loaded, loadFailed } = useEngines();
  const options = repoOptions(engines, defaultEngineKey);
  return { status: statusOf(loaded, loadFailed, options), options };
}

function useWorkspaceListing(enabled: boolean, preselected: string): TargetListing {
  const { data, isError } = useOperatorWorkspaceTargetsQuery(enabled);
  const options = workspaceOptions(data ?? [], preselected);
  return { status: statusOf(data !== undefined, isError, options), options };
}

export function useOperatorTargets(scope: OperatorScope, preselected: string): OperatorTargets {
  const repos = useRepoListing();
  const workspaces = useWorkspaceListing(scope === "workspace", preselected);
  if (scope === "fleet") return NO_TARGETS;
  const listing = scope === "repo" ? repos : workspaces;
  return { ...listing, initial: initialValue(scope, listing.options, preselected) };
}
