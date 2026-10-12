import { useState } from "react";
import { Link } from "react-router-dom";

import type { RecurringStatus } from "../../../api";
import { RefreshErrorLine, RefreshingLabel, refreshFlags } from "../../../components/RefreshCue";
import Disclosure from "../../../ui/Disclosure";
import SectionHeading from "../../../ui/SectionHeading";
import AggregateChip from "../../../components/AggregateChip";
import {
  backupCoverageText,
  mergedSummaryLine,
  recurringRows,
  summaryCounts,
  type RecurringRow,
} from "../../../lib/recurringFields";
import { useRecurringStatus } from "../../../hooks/useRecurringStatus";

interface RecurringRowFieldProps {
  readonly label: string;
  readonly value: string;
}

function RecurringRowField({ label, value }: Readonly<RecurringRowFieldProps>) {
  return (
    <div>
      <dt className="text-[10px] font-medium uppercase tracking-wide text-text-muted">{label}</dt>
      <dd className="text-text-primary">{value}</dd>
    </div>
  );
}

const LINK_CLASS = "inline-flex min-h-11 items-center text-sm font-medium text-btn-action underline";

function RecurringLabelLink({ row }: Readonly<{ row: RecurringRow }>) {
  const { destination } = row;
  if (destination.kind === "none") {
    return (
      <span className="text-sm font-medium text-text-primary">
        {row.label}
        {destination.reason != null && (
          <span className="ml-1 font-normal text-text-muted">— {destination.reason}</span>
        )}
      </span>
    );
  }
  const ariaLabel = `${row.label} — ${destination.linkContext}`;
  if (destination.kind === "external") {
    return (
      <a
        href={destination.href}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={ariaLabel}
        className={LINK_CLASS}
      >
        {row.label}
      </a>
    );
  }
  return (
    <Link to={destination.href} aria-label={ariaLabel} className={LINK_CLASS}>
      {row.label}
    </Link>
  );
}

interface RecurringRowViewProps {
  readonly row: RecurringRow;
  readonly nested?: boolean;
}

function RecurringRowView({ row, nested }: Readonly<RecurringRowViewProps>) {
  return (
    <li
      data-testid={`recurring-row-${row.processId}`}
      className={nested ? "ml-4 border-l border-border/60 pl-3" : undefined}
    >
      <div className="flex flex-wrap items-start gap-2">
        <RecurringLabelLink row={row} />
        <AggregateChip
          state={row.runStateTone}
          label={row.runStateText}
          testIdSuffix={`recurring-outcome-${row.processId}`}
        />
        <AggregateChip
          state={row.overdueTone}
          label={row.overdueText}
          testIdSuffix={`recurring-overdue-${row.processId}`}
        />
      </div>
      <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <RecurringRowField label="Cadence" value={row.cadenceText} />
        <RecurringRowField label="Last ran" value={row.lastRunText} />
        <RecurringRowField label="Outcome" value={row.outcomeText} />
        <RecurringRowField label="Duration" value={row.durationText} />
      </dl>
      {row.subRows.length > 0 && (
        <ul className="mt-2 space-y-2">
          {row.subRows.map((sub) => (
            <RecurringRowView key={sub.processId} row={sub} nested />
          ))}
        </ul>
      )}
    </li>
  );
}

interface RecurringFootnotesProps {
  readonly engineKey: string;
  readonly serviceScopeNote: string | null | undefined;
  readonly backupCoverage: Parameters<typeof backupCoverageText>[0];
}

function RecurringFootnotes({ engineKey, serviceScopeNote, backupCoverage }: Readonly<RecurringFootnotesProps>) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {serviceScopeNote != null && (
        <p className="text-xs text-text-muted" data-testid={`recurring-service-scope-note-${engineKey}`}>
          {serviceScopeNote}
        </p>
      )}
      <p className="text-xs text-text-muted" data-testid={`recurring-backup-coverage-${engineKey}`}>
        {backupCoverageText(backupCoverage)}
      </p>
    </div>
  );
}

interface RecurringDisclosureProps {
  readonly engineKey: string;
  readonly status: RecurringStatus;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
}

function RecurringDisclosure({
  engineKey,
  status,
  open,
  onOpenChange,
}: Readonly<RecurringDisclosureProps>) {
  const rows = recurringRows(status, engineKey);
  const empty = rows.length === 0;
  return (
    <Disclosure
      preset="panelXs"
      defaultOpen={false}
      open={open}
      onOpenChange={onOpenChange}
      testId={`recurring-processes-disclosure-${engineKey}`}
      summaryTestId={empty ? `recurring-empty-${engineKey}` : undefined}
      label={
        empty
          ? "No recurring processes are tracked for this repository yet."
          : `Recurring processes — ${mergedSummaryLine(summaryCounts(rows), rows)}`
      }
    >
      {!empty && (
        <ul className="mt-2 space-y-3">
          {rows.map((row) => (
            <RecurringRowView key={row.processId} row={row} />
          ))}
        </ul>
      )}
      <RecurringFootnotes
        engineKey={engineKey}
        serviceScopeNote={status.service_scope_note}
        backupCoverage={status.backup_coverage}
      />
    </Disclosure>
  );
}

export interface RecurringProcessesPanelProps {
  readonly engineKey: string;
}

export default function RecurringProcessesPanel({ engineKey }: Readonly<RecurringProcessesPanelProps>) {
  const { state } = useRecurringStatus(engineKey);
  const { refreshing, refreshError } = refreshFlags(state);
  const [open, setOpen] = useState(false);

  if (state.status === "loading") {
    return (
      <p className="text-xs text-text-muted" data-testid={`recurring-status-loading-${engineKey}`}>
        Recurring processes — loading…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p className="text-xs text-text-muted" data-testid={`recurring-status-error-${engineKey}`}>
        Recurring-process status unavailable.
      </p>
    );
  }

  return (
    <div
      className="space-y-2 rounded-md border border-border/60 bg-surface/40 p-2"
      data-testid={`recurring-processes-panel-${engineKey}`}
      aria-busy={refreshing}
    >
      <div className="flex items-center gap-2">
        <SectionHeading level="h2">Recurring Processes</SectionHeading>
        {refreshing && <RefreshingLabel testId={`recurring-status-refreshing-${engineKey}`} />}
      </div>
      <RecurringDisclosure
        engineKey={engineKey}
        status={state.data}
        open={open}
        onOpenChange={setOpen}
      />
      {refreshError !== null && (
        <RefreshErrorLine
          testId={`recurring-status-refresh-error-${engineKey}`}
          message={refreshError}
        />
      )}
    </div>
  );
}
