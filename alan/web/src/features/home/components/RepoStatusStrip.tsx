import { Link } from "react-router-dom";

import type { RepoStatus } from "../../../api";
import { RefreshErrorLine, RefreshingLabel, refreshFlags } from "../../../components/RefreshCue";
import AggregateChip, { type ChipTone } from "../../../components/AggregateChip";
import { failingRows, recurringRows, summaryCounts, type RecurringRow } from "../../../lib/recurringFields";
import {
  costChip,
  deliveryTimeChips,
  lastDeployChip,
  mainCiChip,
  openPrsChip,
  type ChipView,
} from "./statusFields";
import UnblockerSummaryRow from "./UnblockerSummaryRow";
import { useRecurringStatus } from "../../../hooks/useRecurringStatus";
import { useRepoStatus } from "../hooks/useRepoStatus";

interface StatusGroupProps {
  readonly label: string;
  readonly chip: ChipView;
  readonly testIdSuffix: string;
}

function StatusGroup({ label, chip, testIdSuffix }: Readonly<StatusGroupProps>) {
  return (
    <div className="flex shrink-0 items-start gap-2" data-testid={`repo-status-group-${testIdSuffix}`}>
      <span className="text-[10px] font-medium uppercase tracking-wide text-text-muted">{label}</span>
      <AggregateChip state={chip.tone} label={chip.label} testIdSuffix={testIdSuffix} />
    </div>
  );
}

interface StatusGroupChipsItem {
  readonly chip: ChipView;
  readonly key: string;
}

interface StatusGroupChipsProps {
  readonly label: string;
  readonly items: readonly StatusGroupChipsItem[];
  readonly testIdPrefix: string;
}

function StatusGroupChips({ label, items, testIdPrefix }: Readonly<StatusGroupChipsProps>) {
  return (
    <div className="flex shrink-0 items-start gap-2" data-testid={`repo-status-group-${testIdPrefix}`}>
      <span className="text-[10px] font-medium uppercase tracking-wide text-text-muted">{label}</span>
      <div className="flex flex-wrap gap-2">
        {items.map(({ chip, key }) => (
          <AggregateChip
            key={key}
            state={chip.tone}
            label={chip.label}
            testIdSuffix={`${testIdPrefix}-${key}`}
          />
        ))}
      </div>
    </div>
  );
}

function deliveryTimeItems(status: RepoStatus): StatusGroupChipsItem[] {
  const bySize = status.delivery_time.by_size;
  return deliveryTimeChips(status.delivery_time).map((chip, index) => ({
    chip,
    key: bySize[index]!.size,
  }));
}

interface RecurringGroupProps {
  readonly label: string;
  readonly chip: ChipView;
  readonly testIdSuffix: string;
  readonly healthHref: string;
}

function RecurringGroup({ label, chip, testIdSuffix, healthHref }: Readonly<RecurringGroupProps>) {
  return (
    <Link
      to={healthHref}
      className="flex min-h-11 shrink-0 items-center gap-2 hover:underline"
      data-testid={`repo-status-group-${testIdSuffix}`}
    >
      <span className="text-[10px] font-medium uppercase tracking-wide text-text-muted">{label}</span>
      <AggregateChip state={chip.tone} label={chip.label} testIdSuffix={testIdSuffix} />
    </Link>
  );
}

function flattenRecurringRows(rows: readonly RecurringRow[]): readonly RecurringRow[] {
  return rows.flatMap((row) => [row, ...flattenRecurringRows(row.subRows)]);
}

function succeededRecurringCount(rows: readonly RecurringRow[]): number {
  return flattenRecurringRows(rows).filter((row) => row.runState === "succeeded").length;
}

const MAX_NAMED_FAILURES_IN_STRIP = 2;

function failingRecurringLabel(failing: readonly RecurringRow[]): string {
  const shown = failing.slice(0, MAX_NAMED_FAILURES_IN_STRIP).map((row) => row.label);
  const extra = failing.length - shown.length;
  return extra > 0 ? `${shown.join(", ")} +${String(extra)} more` : shown.join(", ");
}

interface RecurringCountEntry {
  readonly key: "total" | "succeeded" | "failed" | "overdue";
  readonly label: string;
  readonly value: number;
  readonly tone: ChipTone;
}

function recurringCountEntries(
  totalCount: number,
  succeededCount: number,
  failedCount: number,
  overdueCount: number,
): RecurringCountEntry[] {
  const entries: RecurringCountEntry[] = [
    { key: "total", label: "Recurring", value: totalCount, tone: "unknown" },
    { key: "succeeded", label: "Succeeded", value: succeededCount, tone: "green" },
  ];
  if (failedCount > 0) entries.push({ key: "failed", label: "Failed", value: failedCount, tone: "red" });
  if (overdueCount > 0) {
    entries.push({ key: "overdue", label: "Overdue", value: overdueCount, tone: "transitional" });
  }
  return entries;
}

function RecurringSummaryRow({ engineKey }: Readonly<{ engineKey: string }>) {
  const { state } = useRecurringStatus(engineKey);
  if (state.status === "loading" || state.status === "error") return null;

  const rows = recurringRows(state.data, engineKey);
  const counts = summaryCounts(rows);
  const failing = failingRows(rows);
  const healthHref = `/health?engine=${encodeURIComponent(engineKey)}`;
  const entries = recurringCountEntries(
    counts.total,
    succeededRecurringCount(rows),
    counts.failed,
    counts.overdue,
  );

  return (
    <div className="flex flex-wrap items-start gap-4">
      {entries.map((entry) => (
        <RecurringGroup
          key={entry.key}
          label={entry.label}
          chip={{ tone: entry.tone, label: String(entry.value) }}
          testIdSuffix={`${engineKey}-recurring-${entry.key}`}
          healthHref={healthHref}
        />
      ))}
      {failing.length > 0 && (
        <RecurringGroup
          label="Failing"
          chip={{ tone: "red", label: failingRecurringLabel(failing) }}
          testIdSuffix={`${engineKey}-recurring-failing`}
          healthHref={healthHref}
        />
      )}
    </div>
  );
}

function StatusGroups({ status }: Readonly<{ status: RepoStatus }>) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0 space-y-2">
      <div className="flex flex-wrap items-start gap-4">
        <StatusGroup label="Main CI" chip={mainCiChip(status.main_ci)} testIdSuffix={`${status.slug}-main-ci`} />
        <StatusGroup
          label="Open PRs"
          chip={openPrsChip(status.open_prs)}
          testIdSuffix={`${status.slug}-open-prs`}
        />
        <StatusGroup
          label="Last deploy"
          chip={lastDeployChip(status.last_deploy)}
          testIdSuffix={`${status.slug}-last-deploy`}
        />
        <StatusGroup label="Cost" chip={costChip(status.cost)} testIdSuffix={`${status.slug}-cost`} />
      </div>
      <div className="flex flex-wrap items-start gap-4">
        <StatusGroupChips
          label="Delivery time"
          items={deliveryTimeItems(status)}
          testIdPrefix={`${status.slug}-delivery`}
        />
      </div>
      <RecurringSummaryRow engineKey={status.slug} />
      <UnblockerSummaryRow engineKey={status.slug} />
    </fieldset>
  );
}

export interface RepoStatusStripProps {
  readonly engineKey: string;
}

export default function RepoStatusStrip({ engineKey }: Readonly<RepoStatusStripProps>) {
  const { state } = useRepoStatus(engineKey);
  const { refreshing, refreshError } = refreshFlags(state);

  if (state.status === "loading") {
    return (
      <p className="text-xs text-text-muted" data-testid={`repo-status-loading-${engineKey}`}>
        Repository status — loading…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p className="text-xs text-text-muted" data-testid={`repo-status-error-${engineKey}`}>
        Repository status unavailable.
      </p>
    );
  }

  const status = state.data;
  return (
    <div
      className="space-y-2 rounded-md border border-border/60 bg-surface/40 p-2"
      data-testid={`repo-status-strip-${status.slug}`}
      aria-busy={refreshing}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="min-w-0 truncate text-xs font-semibold uppercase tracking-wide text-text-muted">
          Repository Status
        </h2>
        {refreshing && <RefreshingLabel testId={`repo-status-refreshing-${status.slug}`} />}
      </div>
      {refreshError !== null && (
        <RefreshErrorLine
          testId={`repo-status-refresh-error-${status.slug}`}
          message={refreshError}
        />
      )}
      <StatusGroups status={status} />
    </div>
  );
}
