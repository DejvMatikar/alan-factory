import { Fragment, useId } from "react";

import type { HealthMetricWindows, HealthScopeWindows, HealthWindow } from "../../../api";
import {
  LONG_WINDOW_DAYS,
  LOW_DATA_LABEL,
  NOT_MEASURED_YET,
  SHORT_WINDOW_DAYS,
  comparisonSentence,
  coverageNote,
  formatPercent,
  readFunnel,
  readWindow,
  verdictDisplay,
} from "../../../lib/metricWindow";
import {
  FUNNEL_STAGES,
  METRIC_GROUPS,
  OUTCOME_FLOOR,
  type MetricGroupDef,
  type MetricRowDef,
} from "./repoMetricRows";

const CELL_CLASS = "py-2 pr-4 align-top";
const NOTE_CLASS = "text-xs text-text-muted";
const HEAD_CLASS = "py-2 pr-4 text-left font-medium";

interface ValueCellProps {
  readonly testId: string;
  readonly data: HealthWindow;
  readonly days: number;
  readonly flagFloor: boolean;
}

function ValueCell({ testId, data, days, flagFloor }: Readonly<ValueCellProps>) {
  const reading = readWindow(data);
  if (!reading.measured) {
    return (
      <td data-testid={testId} className={`${CELL_CLASS} text-text-muted`}>
        {NOT_MEASURED_YET}
      </td>
    );
  }
  const note = coverageNote(data, days);
  const belowFloor = flagFloor && !reading.lowData && reading.value < OUTCOME_FLOOR;
  return (
    <td data-testid={testId} data-low-data={reading.lowData} className={CELL_CLASS}>
      <span
        className={
          reading.lowData
            ? "tabular-nums text-text-muted"
            : "tabular-nums font-medium text-text-primary"
        }
      >
        {reading.percent}
      </span>{" "}
      <span className={NOTE_CLASS}>n={reading.sample}</span>
      {reading.lowData && (
        <span className={NOTE_CLASS}>
          {" "}
          {LOW_DATA_LABEL}
        </span>
      )}
      {belowFloor && (
        <span data-testid={`${testId}-floor`} className="text-xs font-medium text-btn-destroy">
          {" "}
          below the {formatPercent(OUTCOME_FLOOR)} floor
        </span>
      )}
      {note !== null && <span className={`block ${NOTE_CLASS}`}>{note}</span>}
    </td>
  );
}

interface VerdictCellProps {
  readonly testId: string;
  readonly label: string;
  readonly metric: HealthMetricWindows;
}

function VerdictCell({ testId, label, metric }: Readonly<VerdictCellProps>) {
  const { symbol, word } = verdictDisplay(metric.verdict);
  return (
    <td data-testid={testId} data-verdict={metric.verdict} className="py-2 align-top">
      <span aria-hidden="true">
        {symbol !== null && `${symbol} `}
        {word}
      </span>
      <span className="sr-only">{comparisonSentence(label, metric)}</span>
    </td>
  );
}

interface FunnelCellProps {
  readonly testId: string;
  readonly count: number;
  readonly dispatched: number;
}

function FunnelCell({ testId, count, dispatched }: Readonly<FunnelCellProps>) {
  const reading = readFunnel(count, dispatched);
  const dimmed = !reading.measured || reading.lowData;
  return (
    <td
      data-testid={testId}
      data-low-data={reading.lowData}
      className={`py-1 pr-4 text-xs ${dimmed ? "text-text-muted" : "text-text-primary"}`}
    >
      {reading.text}
      {reading.lowData && ` ${LOW_DATA_LABEL}`}
    </td>
  );
}

function FunnelRows({ funnel }: Readonly<{ funnel: HealthScopeWindows["funnel"] }>) {
  return (
    <>
      {FUNNEL_STAGES.map(({ key, label }) => (
        <tr key={key} data-testid={`repo-metric-funnel-${key}`}>
          <th scope="row" className="py-1 pr-4 pl-6 text-left text-xs font-normal text-text-muted">
            {label}
          </th>
          <FunnelCell
            testId={`repo-metric-funnel-${key}-7d`}
            count={funnel.d7[key]}
            dispatched={funnel.d7.dispatched}
          />
          <FunnelCell
            testId={`repo-metric-funnel-${key}-30d`}
            count={funnel.d30[key]}
            dispatched={funnel.d30.dispatched}
          />
          <td />
        </tr>
      ))}
    </>
  );
}

interface MetricRowProps {
  readonly def: MetricRowDef;
  readonly metric: HealthMetricWindows;
}

function MetricRow({ def, metric }: Readonly<MetricRowProps>) {
  const nameId = useId();
  const summaryId = useId();
  const testKey = `repo-metric-${def.key}`;
  const flagFloor = def.key === "outcome";
  return (
    <tr data-testid={`repo-metric-row-${def.key}`} className="border-b border-border">
      <th
        scope="row"
        aria-labelledby={nameId}
        aria-describedby={summaryId}
        className="py-2 pr-4 text-left font-normal align-top"
      >
        <span id={nameId} className="block font-medium text-text-primary">
          {def.label}
        </span>
        <span id={summaryId} data-testid={`${testKey}-summary`} className={`block ${NOTE_CLASS}`}>
          {def.summary}
        </span>
      </th>
      <ValueCell
        testId={`${testKey}-7d`}
        data={metric.d7}
        days={SHORT_WINDOW_DAYS}
        flagFloor={flagFloor}
      />
      <ValueCell
        testId={`${testKey}-30d`}
        data={metric.d30}
        days={LONG_WINDOW_DAYS}
        flagFloor={flagFloor}
      />
      <VerdictCell testId={`${testKey}-verdict`} label={def.label} metric={metric} />
    </tr>
  );
}

interface MetricGroupProps {
  readonly group: MetricGroupDef;
  readonly windows: HealthScopeWindows;
}

function MetricGroup({ group, windows }: Readonly<MetricGroupProps>) {
  return (
    <tbody data-testid={`repo-metric-group-${group.title.toLowerCase()}`}>
      <tr>
        <th
          scope="rowgroup"
          colSpan={4}
          className="pt-4 pb-1 text-left text-xs font-medium uppercase text-text-muted"
        >
          {group.title}
        </th>
      </tr>
      {group.rows.map((def) => (
        <Fragment key={def.key}>
          <MetricRow def={def} metric={windows[def.key]} />
          {def.key === "outcome" && <FunnelRows funnel={windows.funnel} />}
        </Fragment>
      ))}
    </tbody>
  );
}

export interface RepoMetricsTableProps {
  readonly windows: HealthScopeWindows;
}

export default function RepoMetricsTable({ windows }: Readonly<RepoMetricsTableProps>) {
  return (
    <section
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable so a keyboard-only user can scroll this table's columns past the fold (axe scrollable-region-focusable)
      tabIndex={0}
      aria-label="Repository metrics table, scroll horizontally for more columns"
      className="relative overflow-x-auto"
    >
      <table data-testid="repo-metrics-table" className="w-full min-w-[36rem] text-left text-sm">
        <thead className="border-b border-border text-xs uppercase text-text-muted">
          <tr>
            <th scope="col" className={HEAD_CLASS}>
              Metric
            </th>
            <th scope="col" className={HEAD_CLASS}>
              Last 7 days
            </th>
            <th scope="col" className={HEAD_CLASS}>
              Last 30 days
            </th>
            <th scope="col" className="py-2 text-left font-medium">
              7d vs 30d
            </th>
          </tr>
        </thead>
        {METRIC_GROUPS.map((group) => (
          <MetricGroup key={group.title} group={group} windows={windows} />
        ))}
      </table>
    </section>
  );
}
