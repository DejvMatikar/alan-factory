import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import type { NodeStatus, UnblockRecord, WorkspaceSummary } from "../../../api";
import { formatAutoArchiveCountdown, formatRelativeTime } from "../../../lib/format";
import { SCROLL_BODY_CLASS, TABLE_HEADER_Z_CLASS } from "../../../shell/stickyOffsets";
import { isHostOwnedOutcome, isHumanOwnedPark, SILENCE_THRESHOLD_MS, unblockView } from "../../../lib/unblockView";
import NodeStatusBadge from "../../../components/NodeStatusBadge";
import StatusBadge from "../../../components/StatusBadge";
import ActiveTimeCell from "./ActiveTimeCell";
import AggregateChip from "../../../components/AggregateChip";
import AutoArchiveBadge from "./AutoArchiveBadge";
import {
  activeSecondsForFeature,
  doneCellForFeature,
  doneCellForProject,
  EMPTY_CELL,
  featurePrimaryLabel,
  formatCostCell,
  formatUsd,
  noteFirstLine,
  phaseCellForProject,
} from "./cells";
import {
  aggregateStateForStatuses,
  isRepoSectionEmpty,
  NEEDS_YOU_STATUSES,
  sumActiveSeconds,
  type ProjectGroup,
  type RepoSectionModel,
} from "../../../lib/fold";
import HomeRow, { type HomeRowCells } from "./HomeRow";
import { rowActionAriaLabel, rowActionFor } from "./rowAction";
import type { Tab } from "./tabs";
import { useRowAction } from "../hooks/useRowAction";

function columnHeaders(showProjects: boolean): string[] {
  return [
    showProjects ? "Project · Feature" : "Feature",
    "Team",
    "State",
    "Phase",
    "Done",
    "Active",
    "Cost",
    "Upd",
  ];
}

const EMPTY_MESSAGE_FOR_TAB: Record<Tab, string> = {
  active: "No active workspaces yet.",
  archived: "No archived workspaces yet.",
};

function nodeStatusForBadge(status: WorkspaceSummary["latest_invocation_status"]): NodeStatus | null {
  if (status == null) return null;
  return status === "escalated" ? "failed" : status;
}

function FeatureTeamCell({
  ws,
}: Readonly<{ ws: Pick<WorkspaceSummary, "latest_team" | "latest_invocation_status"> }>) {
  const badgeStatus = nodeStatusForBadge(ws.latest_invocation_status);
  if (ws.latest_team === null || badgeStatus === null) {
    return <span>—</span>;
  }
  return (
    <span className="inline-flex items-center gap-2">
      <span>{ws.latest_team}</span>
      <NodeStatusBadge status={badgeStatus} />
    </span>
  );
}

function DependencySweepBadge({ taskId }: Readonly<{ taskId: string }>) {
  return (
    <span
      className="inline-block shrink-0 rounded-full bg-blue-500/20 px-2 py-0.5 text-xs text-blue-300"
      data-testid={`dep-badge-${taskId}`}
    >
      Auto dependency update
    </span>
  );
}

function hostOwnedParkLabel(unblock: UnblockRecord | null | undefined): string | null {
  if (unblock == null) return null;
  const view = unblockView(unblock, Date.now());
  if (view.owner !== "host") return null;
  return view.displayState === "acted" ? "Host acted" : "Host is on it";
}

function HostOwnedParkBadge({
  taskId,
  label,
}: Readonly<{ taskId: string; label: string }>) {
  return (
    <span
      className="inline-block shrink-0 rounded-full bg-status-active-tint/20 px-2 py-0.5 text-xs text-status-active-fg"
      data-testid={`unblock-owner-badge-${taskId}`}
    >
      {label}
    </span>
  );
}

type FeatureBadgeRowWs = Pick<
  WorkspaceSummary,
  "task_id" | "team" | "auto_archive_days" | "updated_at" | "unblock"
>;

function autoArchiveCountdownFor(ws: Pick<FeatureBadgeRowWs, "updated_at" | "auto_archive_days">): string | null {
  return formatAutoArchiveCountdown(ws.updated_at, ws.auto_archive_days ?? null);
}

function featureBadgesVisible(ws: FeatureBadgeRowWs): boolean {
  return (
    ws.team === "dependency_sweep" ||
    autoArchiveCountdownFor(ws) !== null ||
    hostOwnedParkLabel(ws.unblock) !== null
  );
}

function FeatureBadgeRow({ ws }: Readonly<{ ws: FeatureBadgeRowWs }>) {
  if (!featureBadgesVisible(ws)) return null;
  const autoArchiveDays = ws.auto_archive_days ?? null;
  const countdown = autoArchiveCountdownFor(ws);
  const hostOwnedLabel = hostOwnedParkLabel(ws.unblock);
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-2">
      {ws.team === "dependency_sweep" && <DependencySweepBadge taskId={ws.task_id} />}
      {countdown !== null && autoArchiveDays !== null && (
        <AutoArchiveBadge taskId={ws.task_id} updatedAt={ws.updated_at} days={autoArchiveDays} />
      )}
      {hostOwnedLabel !== null && <HostOwnedParkBadge taskId={ws.task_id} label={hostOwnedLabel} />}
    </span>
  );
}

function UnboundChip({ projectId }: Readonly<{ projectId: string }>) {
  return (
    <span
      className="inline-block rounded px-2 py-0.5 text-xs bg-status-attention-tint/15 text-status-attention-fg"
      data-testid={`unbound-chip-${projectId}`}
    >
      Unbound
    </span>
  );
}

function FeaturePrimaryCell({ ws }: Readonly<{ ws: WorkspaceSummary }>) {
  const line = noteFirstLine(ws);
  return (
    <span className="flex min-w-0 flex-col gap-1">
      <Link
        to={`/workspaces/${encodeURIComponent(ws.task_id)}`}
        aria-label={featurePrimaryLabel(ws)}
        title={ws.title}
        className="inline-flex min-h-11 min-w-11 shrink-0 self-start items-center font-medium text-text-primary hover:underline"
      >
        {ws.task_id}
      </Link>
      {line !== null && (
        <span
          className="min-w-0 line-clamp-1 break-words text-xs text-text-muted"
          title={line}
          data-testid={`ws-note-${ws.task_id}`}
        >
          {line}
        </span>
      )}
      <FeatureBadgeRow ws={ws} />
    </span>
  );
}

function HostOwnedStateCell() {
  return (
    <span
      className="inline-block rounded px-2 py-0.5 text-xs bg-status-active-tint/15 text-status-active-fg"
      data-testid="status-host-owned"
    >
      in progress{" "}
      <span className="ml-1" aria-hidden="true">
        ∼
      </span>
    </span>
  );
}

function stateCellForFeature(ws: Pick<WorkspaceSummary, "status" | "unblock">): ReactNode {
  const hostOwned =
    NEEDS_YOU_STATUSES.has(ws.status) && !isHumanOwnedPark(ws.unblock ?? null, Date.now());
  return hostOwned ? <HostOwnedStateCell /> : <StatusBadge status={ws.status} />;
}

function featureRowCells(ws: WorkspaceSummary): HomeRowCells {
  return {
    primary: <FeaturePrimaryCell ws={ws} />,
    team: <FeatureTeamCell ws={ws} />,
    state: stateCellForFeature(ws),
    phase: EMPTY_CELL,
    done: doneCellForFeature(ws),
    active: (
      <ActiveTimeCell
        seconds={activeSecondsForFeature(ws)}
        variant="value"
        testId={`active-time-${ws.task_id}`}
      />
    ),
    cost: formatCostCell(ws.total_cost_usd ?? null),
    updated: formatRelativeTime(ws.updated_at),
  };
}

interface FeatureRowProps {
  readonly ws: WorkspaceSummary;
  readonly reload: () => void;
}

function FeatureRow({ ws, reload }: Readonly<FeatureRowProps>) {
  const cells = featureRowCells(ws);
  const action = rowActionFor(ws.status);
  const { busy, error, run } = useRowAction(ws.task_id, ws.status, reload);
  return (
    <HomeRow
      kind="feature"
      testId={`home-row-feature-${ws.task_id}`}
      cells={cells}
      action={{
        testId: `workspace-row-action-${ws.task_id}`,
        label: busy ? action.busyLabel : action.label,
        description: action.description,
        disabled: busy,
        busy,
        error,
        onClick: run,
        icon: action.icon,
        ariaLabel: rowActionAriaLabel(action, busy, ws.title),
      }}
    />
  );
}

function projectPrimaryCell(project: ProjectGroup["project"]): ReactNode {
  const link = (
    <Link
      to={`/projects/${encodeURIComponent(project.id)}`}
      className="inline-flex min-h-11 items-center hover:underline"
    >
      {project.title}
    </Link>
  );
  if (project.engine_key != null || project.intended_engine_key == null) return link;
  return (
    <span className="flex min-w-0 items-center gap-2">
      {link}
      <UnboundChip projectId={project.id} />
    </span>
  );
}

function projectRowCells(group: ProjectGroup): HomeRowCells {
  const { project, workspaces } = group;
  const state = aggregateStateForStatuses(
    workspaces.map((ws) => ({ status: ws.status, unblock: ws.unblock ?? null })),
  );
  return {
    primary: projectPrimaryCell(project),
    team: <span>—</span>,
    state: <AggregateChip state={state} testIdSuffix={`project-${project.id}`} />,
    phase: phaseCellForProject(project),
    done: doneCellForProject(project),
    active: (
      <ActiveTimeCell
        seconds={sumActiveSeconds(workspaces)}
        variant="sum"
        testId={`active-time-project-${project.id}`}
      />
    ),
    cost: formatUsd(project.total_spend_usd),
    updated: formatRelativeTime(project.updated_at),
  };
}

interface ProjectGroupRowsProps {
  readonly group: ProjectGroup;
  readonly reload: () => void;
}

function ProjectGroupRows({ group, reload }: Readonly<ProjectGroupRowsProps>) {
  return (
    <>
      <HomeRow
        kind="project"
        testId={`home-row-project-${group.project.id}`}
        cells={projectRowCells(group)}
      />
      {group.workspaces.map((ws) => (
        <FeatureRow key={ws.task_id} ws={ws} reload={reload} />
      ))}
    </>
  );
}

const UNASSIGNED_CELLS: HomeRowCells = {
  primary: <span>Unassigned</span>,
  team: <span>—</span>,
  state: <span>—</span>,
  phase: EMPTY_CELL,
  done: <span>—</span>,
  active: <span>—</span>,
  cost: <span>—</span>,
  updated: <span>—</span>,
};

interface UnassignedGroupRowsProps {
  readonly repoKey: string;
  readonly workspaces: readonly WorkspaceSummary[];
  readonly reload: () => void;
}

function UnassignedGroupRows({ repoKey, workspaces, reload }: Readonly<UnassignedGroupRowsProps>) {
  if (workspaces.length === 0) return null;
  return (
    <>
      <HomeRow kind="unassigned" testId={`home-row-unassigned-${repoKey}`} cells={UNASSIGNED_CELLS} />
      {workspaces.map((ws) => (
        <FeatureRow key={ws.task_id} ws={ws} reload={reload} />
      ))}
    </>
  );
}

const HEADER_CELL_CLASS = `sm:sticky sm:top-0 ${TABLE_HEADER_Z_CLASS} bg-surface py-2 pr-4 font-medium`;

function RepoTableHead({ showProjects }: Readonly<{ showProjects: boolean }>) {
  return (
    <thead className="border-b border-border text-xs uppercase text-text-muted">
      <tr>
        <th className={HEADER_CELL_CLASS}>Actions</th>
        {columnHeaders(showProjects).map((label) => (
          <th key={label} className={HEADER_CELL_CLASS}>
            {label}
          </th>
        ))}
      </tr>
    </thead>
  );
}

interface RepoTableBodyProps {
  readonly section: RepoSectionModel;
  readonly reload: () => void;
  readonly showProjects: boolean;
}

function RepoTableBody({ section, reload, showProjects }: Readonly<RepoTableBodyProps>) {
  if (!showProjects) {
    return (
      <>
        {section.unassigned.map((ws) => (
          <FeatureRow key={ws.task_id} ws={ws} reload={reload} />
        ))}
      </>
    );
  }
  return (
    <>
      {section.projectGroups.map((group) => (
        <ProjectGroupRows key={group.project.id} group={group} reload={reload} />
      ))}
      <UnassignedGroupRows repoKey={section.key} workspaces={section.unassigned} reload={reload} />
    </>
  );
}

export interface RepoWorkspaceTableProps {
  readonly section: RepoSectionModel;
  readonly tab: Tab;
  readonly reload: () => void;
  readonly showProjects: boolean;
  readonly loading: boolean;
}

function sectionUnblockRecords(
  section: RepoSectionModel,
): readonly (UnblockRecord | null | undefined)[] {
  const records: (UnblockRecord | null | undefined)[] = [];
  for (const group of section.projectGroups) {
    for (const ws of group.workspaces) records.push(ws.unblock);
  }
  for (const ws of section.unassigned) records.push(ws.unblock);
  return records;
}

function isPendingHostOwnedRecord(
  record: UnblockRecord | null | undefined,
): record is UnblockRecord {
  return record != null && isHostOwnedOutcome(record.outcome);
}

function nextSilenceDeadlineMs(section: RepoSectionModel, nowMs: number): number | null {
  const futureDeadlines = sectionUnblockRecords(section)
    .filter(isPendingHostOwnedRecord)
    .map((record) => record.at * 1000 + SILENCE_THRESHOLD_MS)
    .filter((deadlineMs) => deadlineMs > nowMs);
  return futureDeadlines.length > 0 ? Math.min(...futureDeadlines) : null;
}

function useRerenderAtNextSilenceBoundary(section: RepoSectionModel): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const deadlineMs = nextSilenceDeadlineMs(section, Date.now());
    if (deadlineMs === null) return;
    const delayMs = deadlineMs - Date.now();
    const timer = globalThis.setTimeout(() => setTick((n) => n + 1), delayMs + 1);
    return () => globalThis.clearTimeout(timer);
  }, [section]);
  return tick;
}

export default function RepoWorkspaceTable({
  section,
  tab,
  reload,
  showProjects,
  loading,
}: Readonly<RepoWorkspaceTableProps>) {
  useRerenderAtNextSilenceBoundary(section);
  if (loading) {
    return (
      <p className="text-sm text-text-muted" data-testid="loading">
        Loading…
      </p>
    );
  }
  if (isRepoSectionEmpty(section)) {
    return (
      <p className="text-sm text-text-muted" data-testid={`repo-empty-${section.key}`}>
        {EMPTY_MESSAGE_FOR_TAB[tab]}
      </p>
    );
  }
  return (
    <div className={SCROLL_BODY_CLASS}>
      <table className="w-full text-left text-sm">
        <RepoTableHead showProjects={showProjects} />
        <tbody>
          <RepoTableBody section={section} reload={reload} showProjects={showProjects} />
        </tbody>
      </table>
    </div>
  );
}
