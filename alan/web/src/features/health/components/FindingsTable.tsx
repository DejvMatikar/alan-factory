import { Link } from "react-router-dom";

import type { HealthFinding } from "../../../api";
import { IN_PROGRESS_BADGE_CLASS } from "../../../components/ChatDraftRows";
import EngineBadge from "../../../components/EngineBadge";
import GithubRef from "../../../components/GithubRef";
import { formatRelativeTime } from "../../../lib/format";
import AggregateChip from "../../../components/AggregateChip";

const _SEVERITY_CLASS: Record<string, string> = {
  critical: "bg-status-danger-tint/15 text-status-danger-fg",
  high: "bg-status-warning-tint/15 text-status-warning-fg",
  medium: "bg-status-moderate-tint/15 text-status-moderate-fg",
};

const _KIND_LABEL: Record<string, string> = {
  build_task: "Build task",
  main_ci: "Main CI",
  deploy: "Deploy",
  recurring_errors: "Recurring errors",
  planner_behavioral_regression: "Planner regression",
  retention_near_cap: "Retention near cap",
  stale_job: "Stale job",
  finisher_regression: "Finisher regression",
  worker_pool: "Worker pool",
  stale_live_process: "Stale live process",
  config_quarantine: "Config quarantined",
  host_toolchain: "Host toolchain",
};

const _REPO_BADGE_KINDS: ReadonlySet<string> = new Set(["main_ci", "deploy"]);

function SeverityBadge({
  findingId,
  severity,
}: Readonly<{ findingId: string; severity: string }>) {
  const cls = _SEVERITY_CLASS[severity] ?? "bg-status-neutral-tint/15 text-status-neutral-fg";
  return (
    <span
      data-testid={`severity-badge-${findingId}`}
      className={`inline-block rounded px-2 py-0.5 text-xs ${cls}`}
    >
      {severity}
    </span>
  );
}

function CategoryBadge({
  findingId,
  kind,
}: Readonly<{ findingId: string; kind: string }>) {
  const label = _KIND_LABEL[kind] ?? kind;
  return (
    <span
      data-testid={`category-badge-${findingId}`}
      className="inline-block rounded px-2 py-0.5 text-xs bg-blue-500/15 text-blue-300"
    >
      {label}
    </span>
  );
}

function RepoBadge({
  findingId,
  repo,
}: Readonly<{ findingId: string; repo: string }>) {
  return (
    <span
      data-testid={`repo-badge-${findingId}`}
      className="inline-block rounded px-2 py-0.5 text-xs bg-scope-repo-tint/15 text-scope-repo-fg"
    >
      {repo}
    </span>
  );
}

function BlockerChips({
  findingId,
  flags,
}: Readonly<{ findingId: string; flags: string[] }>) {
  if (flags.length === 0) return null;
  return (
    <div
      data-testid={`blocker-flags-${findingId}`}
      className="mt-1 flex flex-wrap gap-1"
    >
      {flags.map((flag, i) => (
        <span
          key={`${i}-${flag}`}
          className="inline-block rounded px-2 py-0.5 text-xs bg-amber-500/15 text-amber-300"
        >
          {flag}
        </span>
      ))}
    </div>
  );
}

function InProgressChip({ findingId }: Readonly<{ findingId: string }>) {
  return (
    <span
      data-testid={`in-progress-chip-${findingId}`}
      className={IN_PROGRESS_BADGE_CLASS}
    >
      In progress
    </span>
  );
}

function ObservedAtChip({ finding }: Readonly<{ finding: HealthFinding }>) {
  if (finding.observed_at == null) return null;
  return (
    <AggregateChip
      state="unknown"
      testIdSuffix={`observed-${finding.finding_id}`}
      label={`as of ${formatRelativeTime(finding.observed_at)}`}
    />
  );
}

function FindingLinks({ finding }: Readonly<{ finding: HealthFinding }>) {
  return (
    <span className="flex gap-2 text-sm">
      {finding.workspace_id !== null && (
        <Link
          to={`/workspaces/${finding.workspace_id}`}
          className="text-btn-action underline"
          data-testid={`finding-workspace-link-${finding.finding_id}`}
        >
          Workspace
        </Link>
      )}
      {finding.pr_number !== null && (
        <GithubRef
          repo={finding.engine_key ?? null}
          kind="pull"
          prNumber={finding.pr_number}
          target="_blank"
          rel="noreferrer"
          className="text-btn-action underline"
          testId={`finding-pr-link-${finding.finding_id}`}
        >
          PR #{finding.pr_number}
        </GithubRef>
      )}
    </span>
  );
}

function FindingsTableHead() {
  return (
    <thead>
      <tr className="border-b border-border text-left">
        <th className="pb-2 pr-4">Severity</th>
        <th className="pb-2 pr-4">Description</th>
        <th className="pb-2">Links</th>
      </tr>
    </thead>
  );
}

function FindingRow({
  finding,
  hasNonDefaultEngine,
}: Readonly<{ finding: HealthFinding; hasNonDefaultEngine: boolean }>) {
  return (
    <tr
      data-testid={`finding-row-${finding.finding_id}`}
      className="border-b border-border"
    >
      <td className="py-2 pr-4">
        <div className="flex flex-wrap gap-1">
          <SeverityBadge findingId={finding.finding_id} severity={finding.severity} />
          <CategoryBadge findingId={finding.finding_id} kind={finding.kind} />
          {_REPO_BADGE_KINDS.has(finding.kind) && finding.repo != null && (
            <RepoBadge findingId={finding.finding_id} repo={finding.repo} />
          )}
          {finding.engine_key != null && hasNonDefaultEngine && (
            <EngineBadge engineKey={finding.engine_key} testIdSuffix={finding.finding_id} />
          )}
          {finding.in_flight && <InProgressChip findingId={finding.finding_id} />}
          <ObservedAtChip finding={finding} />
        </div>
      </td>
      <td className="py-2 pr-4">
        {finding.description}
        <BlockerChips findingId={finding.finding_id} flags={finding.blocker_flags} />
      </td>
      <td className="py-2">
        <FindingLinks finding={finding} />
      </td>
    </tr>
  );
}

export interface FindingsTableProps {
  readonly findings: readonly HealthFinding[];
  readonly hasNonDefaultEngine: boolean;
}

export default function FindingsTable({
  findings,
  hasNonDefaultEngine,
}: Readonly<FindingsTableProps>) {
  return (
    <div
      className="overflow-x-auto"
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable so a keyboard-only user can scroll this table's columns past the fold (axe scrollable-region-focusable)
      tabIndex={0}
      aria-label="Findings table, scroll horizontally for more columns"
    >
      <table data-testid="health-findings-table" className="w-full border-collapse text-sm">
        <FindingsTableHead />
        <tbody>
          {findings.map((finding) => (
            <FindingRow
              key={finding.finding_id}
              finding={finding}
              hasNonDefaultEngine={hasNonDefaultEngine}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
