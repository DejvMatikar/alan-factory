import type { UnblockOutcomeCount, UnblockShapeAggregate } from "../../../api";
import AggregateChip from "../../../components/AggregateChip";
import { humanize, outcomeTone } from "../../../lib/unblockerSummaryFields";

const _HEAD_CELL_CLASS = "py-2 pr-4 text-left font-medium";
const _NUMBER_CELL_CLASS = "py-1.5 pr-4 tabular-nums";

const _COLUMN_TITLES = {
  cleared: "Workspaces currently not stuck. Not proof that the unblocker's action worked.",
  stuck: "Workspaces still stuck right now.",
  gone: "Workspaces that no longer exist.",
  retried: "Workspaces where the host retried after an earlier attempt.",
} as const;

function OutcomeCell({
  outcomes,
  testIdPrefix,
}: Readonly<{ outcomes: readonly UnblockOutcomeCount[]; testIdPrefix: string }>) {
  if (outcomes.length === 0) return <span className="text-text-muted">none</span>;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {outcomes.map((entry) => (
        <AggregateChip
          key={entry.outcome}
          state={outcomeTone(entry.outcome)}
          label={`${entry.count} ${humanize(entry.outcome)}`}
          testIdSuffix={`${testIdPrefix}-${entry.outcome}`}
        />
      ))}
    </div>
  );
}

function ParkKindRow({ aggregate }: Readonly<{ aggregate: UnblockShapeAggregate }>) {
  const prefix = `unblocker-aggregate-${aggregate.shape}`;
  return (
    <tr data-testid={prefix} className="border-b border-border/60">
      <th scope="row" className="py-1.5 pr-4 text-left font-medium text-text-primary">
        {aggregate.shape}
      </th>
      <td className={_NUMBER_CELL_CLASS}>{aggregate.total}</td>
      <td className={_NUMBER_CELL_CLASS}>{aggregate.workspaces}</td>
      <td className={_NUMBER_CELL_CLASS}>{aggregate.cleared_workspaces}</td>
      <td className={_NUMBER_CELL_CLASS}>{aggregate.still_stuck_workspaces}</td>
      <td className={_NUMBER_CELL_CLASS}>{aggregate.gone_workspaces}</td>
      <td className={_NUMBER_CELL_CLASS}>{aggregate.retried_workspaces}</td>
      <td className="py-1.5 pr-4">
        <OutcomeCell outcomes={aggregate.attempt_outcomes} testIdPrefix={`${prefix}-attempt`} />
      </td>
      <td className="py-1.5">
        <OutcomeCell outcomes={aggregate.report_outcomes} testIdPrefix={`${prefix}-report`} />
      </td>
    </tr>
  );
}

function ParkKindsHead() {
  return (
    <thead className="border-b border-border text-xs uppercase text-text-muted">
      <tr>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Park kind
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Events
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Workspaces
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS} title={_COLUMN_TITLES.cleared}>
          Cleared
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS} title={_COLUMN_TITLES.stuck}>
          Still stuck
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS} title={_COLUMN_TITLES.gone}>
          Gone
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS} title={_COLUMN_TITLES.retried}>
          Retried
        </th>
        <th scope="col" className={_HEAD_CELL_CLASS}>
          Host attempts
        </th>
        <th scope="col" className="py-2 text-left font-medium">
          Gave up
        </th>
      </tr>
    </thead>
  );
}

export interface UnblockerParkKindsTableProps {
  readonly aggregates: readonly UnblockShapeAggregate[];
  readonly scopeLabel: string;
}

export default function UnblockerParkKindsTable({
  aggregates,
  scopeLabel,
}: Readonly<UnblockerParkKindsTableProps>) {
  if (aggregates.length === 0) return null;
  return (
    <div data-testid="unblocker-aggregates" className="mt-4">
      <h3 className="text-sm font-medium text-text-primary">Park kinds — {scopeLabel}</h3>
      <div
        className="relative mt-2 overflow-x-auto"
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable so a keyboard-only user can scroll this table's columns past the fold (axe scrollable-region-focusable)
        tabIndex={0}
        aria-label="Park kinds table, scroll horizontally for more columns"
      >
        <table data-testid="unblocker-aggregates-table" className="w-full text-left text-sm">
          <ParkKindsHead />
          <tbody>
            {aggregates.map((aggregate) => (
              <ParkKindRow key={aggregate.shape} aggregate={aggregate} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
