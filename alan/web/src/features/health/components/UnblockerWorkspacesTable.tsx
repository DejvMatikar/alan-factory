import { useId, useState } from "react";
import { Link } from "react-router-dom";

import type { UnblockActivityEvent, UnblockAttemptIndex, UnblockScopedActivity } from "../../../api";
import Button from "../../../components/Button";
import AggregateChip, { type ChipTone } from "../../../components/AggregateChip";
import { humanize, outcomeTone, plural } from "../../../lib/unblockerSummaryFields";
import {
  attemptsLabel,
  buildWorkspaceRows,
  clearanceChipOf,
  eventsCutByCap,
  GAVE_UP_TITLE,
  type UnblockerWorkspaceRow,
} from "./unblockerWorkspaceRows";

const COLUMN_COUNT = 10;
const _LINK_CLASS = "inline-flex min-h-11 items-center text-sm font-medium text-btn-action underline";
const _HEAD_CELL_CLASS = "py-2 pr-4 text-left font-medium";
const _CELL_CLASS = "py-1 pr-4 align-middle";

function orDash(value: string | null): string {
  return value ?? "—";
}

function formatEventTime(at: string): string {
  return `${at.replace("T", " ").replace("Z", "")} UTC`;
}

function repetitionTone(attempt: UnblockAttemptIndex): ChipTone {
  if (attempt.fingerprint_index >= attempt.fingerprint_ceiling) return "red";
  return attempt.fingerprint_index > 1 ? "transitional" : "unknown";
}

function repetitionLabel(attempt: UnblockAttemptIndex): string {
  const base = `attempt ${attempt.fingerprint_index} of ${attempt.fingerprint_ceiling}`;
  return attempt.fingerprint_index >= attempt.fingerprint_ceiling
    ? `${base} — the host stops after this`
    : base;
}

function EventChips({ event }: Readonly<{ event: UnblockActivityEvent }>) {
  const clearance = clearanceChipOf(event.clearance);
  return (
    <>
      <AggregateChip
        state={outcomeTone(event.outcome)}
        label={humanize(event.outcome)}
        testIdSuffix={`unblocker-outcome-${event.id}`}
      />
      <AggregateChip
        state={clearance.tone}
        label={clearance.label}
        testIdSuffix={`unblocker-clearance-${event.id}`}
      />
    </>
  );
}

function AttemptDetail({ event }: Readonly<{ event: UnblockActivityEvent }>) {
  const { attempt } = event;
  return (
    <>
      <span>Host did {orDash(event.action)}</span>
      {attempt !== null && (
        <>
          <AggregateChip
            state={repetitionTone(attempt)}
            label={repetitionLabel(attempt)}
            testIdSuffix={`unblocker-repetition-${event.id}`}
          />
          <span>
            workspace attempt {attempt.workspace_index} of {attempt.workspace_ceiling}
          </span>
        </>
      )}
    </>
  );
}

function gaveUpLabel(attemptsTotal: number | null): string {
  if (attemptsTotal === null) return "gave up";
  return `gave up after ${plural(attemptsTotal, "attempt")}`;
}

function ReportDetail({ event }: Readonly<{ event: UnblockActivityEvent }>) {
  return (
    <>
      <AggregateChip
        state="red"
        label={gaveUpLabel(event.attempts_total)}
        testIdSuffix={`unblocker-gave-up-${event.id}`}
      />
      <span>Reason {orDash(event.reason)}</span>
      <span>Authority holder {orDash(event.authority_holder)}</span>
    </>
  );
}

function EventRow({
  event,
  rowId,
  hidden,
}: Readonly<{ event: UnblockActivityEvent; rowId: string; hidden: boolean }>) {
  return (
    <tr
      id={rowId}
      hidden={hidden}
      data-testid={`unblocker-event-${event.id}`}
      className="border-b border-border/60 text-xs"
    >
      <td colSpan={COLUMN_COUNT} className="py-1 pl-12 pr-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <time dateTime={event.at} className="whitespace-nowrap tabular-nums">
            {formatEventTime(event.at)}
          </time>
          <EventChips event={event} />
          {event.kind === "report" ? (
            <ReportDetail event={event} />
          ) : (
            <AttemptDetail event={event} />
          )}
        </div>
      </td>
    </tr>
  );
}

function CapNoteRow({
  row,
  rowId,
  hidden,
}: Readonly<{ row: UnblockerWorkspaceRow; rowId: string; hidden: boolean }>) {
  return (
    <tr
      id={rowId}
      hidden={hidden}
      data-testid={`unblocker-workspace-cap-${row.workspaceKey}`}
      className="border-b border-border/60 text-xs text-text-muted"
    >
      <td colSpan={COLUMN_COUNT} className="py-1 pl-12 pr-4">
        Showing {row.events.length} of {row.eventsTotal} events for this workspace
      </td>
    </tr>
  );
}

function DiagnosisCell({ row }: Readonly<{ row: UnblockerWorkspaceRow }>) {
  const { diagnosis } = row;
  if (diagnosis === null) return <span className="text-text-muted">—</span>;
  const disposition =
    diagnosis.proposed_disposition === null
      ? ""
      : ` · proposed disposition ${humanize(diagnosis.proposed_disposition)}`;
  return (
    <span
      data-testid={`unblocker-diagnosis-${row.workspaceKey}`}
      className="break-words text-xs text-text-muted"
    >
      <span className="text-text-primary">{diagnosis.signature}</span>
      {`${disposition} · ${diagnosis.verified ? "verified" : "unverified"}`}
    </span>
  );
}

function IssueCell({ row }: Readonly<{ row: UnblockerWorkspaceRow }>) {
  if (row.issueUrl === null) return <span className="text-text-muted">—</span>;
  return (
    <a
      href={row.issueUrl}
      target="_blank"
      rel="noopener noreferrer"
      className={_LINK_CLASS}
      data-testid={`unblocker-issue-link-${row.workspaceKey}`}
    >
      <span>Filed issue</span>
      <span aria-hidden="true">&nbsp;↗</span>
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

function GaveUpCell({ row }: Readonly<{ row: UnblockerWorkspaceRow }>) {
  if (!row.gaveUp) return <span className="text-text-muted">No</span>;
  return (
    <span title={GAVE_UP_TITLE}>
      <AggregateChip
        state="red"
        label="Gave up"
        testIdSuffix={`unblocker-workspace-gave-up-${row.workspaceKey}`}
      />
    </span>
  );
}

interface WorkspaceRowProps {
  readonly row: UnblockerWorkspaceRow;
  readonly open: boolean;
  readonly controls: string;
  readonly onToggle: () => void;
}

function WorkspaceRow({ row, open, controls, onToggle }: Readonly<WorkspaceRowProps>) {
  const clearance = clearanceChipOf(row.clearance);
  return (
    <tr className="border-b border-border/60" data-testid={`unblocker-workspace-${row.workspaceKey}`}>
      <td className="py-1 pr-1 align-middle">
        <Button
          variant="action"
          iconOnly
          aria-label={`Events for ${row.workspaceKey}`}
          aria-expanded={open}
          aria-controls={controls}
          onClick={onToggle}
          data-testid={`unblocker-workspace-toggle-${row.workspaceKey}`}
        >
          <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        </Button>
      </td>
      <th scope="row" className={`${_CELL_CLASS} text-left font-normal`}>
        <Link to={`/workspaces/${row.workspaceKey}`} className={`${_LINK_CLASS} break-all`}>
          {row.workspaceKey}
        </Link>
      </th>
      <td className={_CELL_CLASS}>
        <time dateTime={row.lastAt} className="whitespace-nowrap tabular-nums">
          {formatEventTime(row.lastAt)}
        </time>
      </td>
      <td className={_CELL_CLASS}>{row.shape}</td>
      <td className={`${_CELL_CLASS} tabular-nums`}>{row.eventsTotal}</td>
      <td className={`${_CELL_CLASS} whitespace-nowrap`}>{attemptsLabel(row)}</td>
      <td className={_CELL_CLASS}>
        <AggregateChip
          state={clearance.tone}
          label={clearance.label}
          testIdSuffix={`unblocker-workspace-clearance-${row.workspaceKey}`}
        />
      </td>
      <td className={_CELL_CLASS}>
        <GaveUpCell row={row} />
      </td>
      <td className={_CELL_CLASS}>
        <IssueCell row={row} />
      </td>
      <td className="py-1 align-middle">
        <DiagnosisCell row={row} />
      </td>
    </tr>
  );
}

function WorkspaceBody({ row }: Readonly<{ row: UnblockerWorkspaceRow }>) {
  const [open, setOpen] = useState(false);
  const baseId = useId();
  const capId = `${baseId}-cap`;
  const capped = eventsCutByCap(row);
  const eventRows = row.events.map((event) => ({ event, rowId: `${baseId}-event-${event.id}` }));
  const eventIds = eventRows.map(({ rowId }) => rowId);
  const controls = (capped ? [capId, ...eventIds] : eventIds).join(" ");
  return (
    <tbody data-testid={`unblocker-workspace-group-${row.workspaceKey}`}>
      <WorkspaceRow
        row={row}
        open={open}
        controls={controls}
        onToggle={() => {
          setOpen((previous) => !previous);
        }}
      />
      {capped && <CapNoteRow row={row} rowId={capId} hidden={!open} />}
      {eventRows.map(({ event, rowId }) => (
        <EventRow key={event.id} event={event} rowId={rowId} hidden={!open} />
      ))}
    </tbody>
  );
}

function WorkspacesHead() {
  return (
    <thead className="border-b border-border text-xs uppercase text-text-muted">
      <tr>
        <th scope="col" className="w-11 py-2 pr-1">
          <span className="sr-only">Expand events</span>
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Workspace
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Last acted on
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Park kind
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Events
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Attempts
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Now
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Gave up
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Issue
        </th>
        <th scope="col" className="py-2 text-left font-medium">
          Latest diagnosis
        </th>
      </tr>
    </thead>
  );
}

export default function UnblockerWorkspacesTable({
  activity,
}: Readonly<{ activity: UnblockScopedActivity }>) {
  const rows = buildWorkspaceRows(activity);
  if (rows.length === 0) return null;
  const listed = activity.events.length;
  return (
    <div data-testid="unblocker-workspaces" className="mt-4">
      <h3 className="text-sm font-medium text-text-primary">
        Workspaces — {activity.repo}
        <span className="ml-2 font-normal text-text-muted">
          {rows.length} touched · still stuck and gave up first, then most recent
        </span>
      </h3>
      {listed < activity.events_total && (
        <p data-testid="unblocker-events-cap" className="mt-1 text-xs text-text-muted">
          Expanded events show the latest {listed} of {activity.events_total}. Each workspace&apos;s
          event and attempt counts cover the whole window.
        </p>
      )}
      <div
        className="relative mt-2 overflow-x-auto"
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable so a keyboard-only user can scroll this table's columns past the fold (axe scrollable-region-focusable)
        tabIndex={0}
        aria-label="Workspaces table, scroll horizontally for more columns"
      >
        <table data-testid="unblocker-workspaces-table" className="w-full text-left text-sm">
          <WorkspacesHead />
          {rows.map((row) => (
            <WorkspaceBody key={row.workspaceKey} row={row} />
          ))}
        </table>
      </div>
    </div>
  );
}
