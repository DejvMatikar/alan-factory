import { Link } from "react-router-dom";

import type {
  HealthMetricWindows,
  HealthScopeWindows,
  QualityMetrics,
  RecoverySnapshot,
  RepoHealthRow,
  ResidueItem,
} from "../../../api";
import { FreshnessIndicator } from "./HealthMetricsPanel";
import DigestSignalSection from "./DigestSignalSection";
import { orderedHealthRepoRows, SEVERITY_WEIGHT } from "./healthRepoRows";
import RepoHealthCell, { RepoWindowCell } from "./RepoHealthCell";
import RepoHealthLegend from "./RepoHealthLegend";

function formatCount(value: number): string {
  return String(Math.round(value));
}

function fmtCountOrDash(value: number | null | undefined): string {
  return value == null ? "—" : formatCount(value);
}

const _FINDINGS_HEADER_TITLE = `Weighted by severity — critical ×${SEVERITY_WEIGHT.critical}, high ×${SEVERITY_WEIGHT.high}, medium ×${SEVERITY_WEIGHT.medium}`;

function RepoHealthTableHead() {
  return (
    <thead className="border-b border-border text-xs uppercase text-text-muted">
      <tr>
        <th scope="col" className="py-2 pr-4 text-left font-medium">
          Repository
        </th>
        <th scope="col" className="py-2 pr-4 text-left font-medium" title={_FINDINGS_HEADER_TITLE}>
          Findings
        </th>
        <th scope="col" className="py-2 pr-4 text-left font-medium">
          Build success
        </th>
        <th scope="col" className="py-2 pr-4 text-left font-medium">
          30-day outcome
        </th>
        <th scope="col" className="py-2 pr-4 text-left font-medium">
          Autonomous
        </th>
        <th scope="col" className="py-2 pr-4 text-left font-medium">
          In flight
        </th>
        <th scope="col" className="py-2 text-left font-medium">
          First-pass
        </th>
      </tr>
    </thead>
  );
}

interface RateCellProps {
  readonly label: string;
  readonly metric: HealthMetricWindows;
  readonly testId: string;
  readonly tdTestId?: string;
  readonly className?: string;
}

function RateCell({
  label,
  metric,
  testId,
  tdTestId,
  className = "py-2 pr-4",
}: Readonly<RateCellProps>) {
  return (
    <td className={className} data-testid={tdTestId}>
      <RepoWindowCell label={label} metric={metric} testId={testId} />
    </td>
  );
}

function RepoHealthDataRow({ row }: Readonly<{ row: RepoHealthRow }>) {
  const { windows } = row;
  return (
    <tr data-testid={`repo-health-row-${row.repo}`} className="border-b border-border">
      <th scope="row" className="py-2 pr-4 text-left font-normal">
        <Link
          to={`/health?engine=${encodeURIComponent(row.repo)}`}
          className="inline-flex min-h-11 items-center font-medium text-text-primary hover:underline"
          data-testid={`repo-health-link-${row.repo}`}
        >
          {row.repo}
        </Link>
      </th>
      <td className="py-2 pr-4">
        <RepoHealthCell
          value={row.outstanding_findings.value}
          sample={row.outstanding_findings.sample}
          lowData={row.outstanding_findings.low_data}
          format={formatCount}
          testId={`repo-health-cell-findings-${row.repo}`}
        />
      </td>
      <RateCell
        label="Build success"
        metric={windows.build_success}
        testId={`repo-health-cell-build-${row.repo}`}
      />
      <RateCell
        label="30-day outcome"
        metric={windows.outcome}
        testId={`repo-health-cell-outcome-${row.repo}`}
      />
      <RateCell
        label="Autonomous"
        metric={windows.autonomous}
        testId={`repo-health-cell-autonomous-${row.repo}`}
      />
      <td className="py-2 pr-4">
        <RepoHealthCell
          value={row.in_flight.value}
          sample={row.in_flight.sample}
          lowData={row.in_flight.low_data}
          format={formatCount}
          testId={`repo-health-cell-inflight-${row.repo}`}
        />
      </td>
      <RateCell
        label="First-pass"
        metric={windows.first_pass_approval}
        testId={`repo-health-cell-firstpass-${row.repo}`}
        className="py-2"
      />
    </tr>
  );
}

interface FleetFooterRowProps {
  readonly blockedCount: number;
  readonly fleet: HealthScopeWindows;
  readonly qualityMetrics: QualityMetrics | null;
}

function FleetFooterRow({ blockedCount, fleet, qualityMetrics }: Readonly<FleetFooterRowProps>) {
  return (
    <tr data-testid="repo-health-footer-row" className="text-text-muted">
      <th scope="row" className="py-2 pr-4 text-left font-normal italic">
        All repositories
      </th>
      <td className="py-2 pr-4" data-testid="repo-health-footer-findings">
        <span className="tabular-nums">{fmtCountOrDash(blockedCount)}</span>{" "}
        <span className="text-xs">(unweighted)</span>
      </td>
      <RateCell
        label="Build success"
        metric={fleet.build_success}
        testId="repo-health-footer-build-value"
        tdTestId="repo-health-footer-build"
      />
      <RateCell
        label="30-day outcome"
        metric={fleet.outcome}
        testId="repo-health-footer-outcome-value"
        tdTestId="repo-health-footer-outcome"
      />
      <RateCell
        label="Autonomous"
        metric={fleet.autonomous}
        testId="repo-health-footer-autonomous-value"
        tdTestId="repo-health-footer-autonomous"
      />
      <td className="py-2 pr-4 tabular-nums" data-testid="repo-health-footer-inflight">
        {fmtCountOrDash(qualityMetrics?.in_flight_build_count)}
      </td>
      <RateCell
        label="First-pass"
        metric={fleet.first_pass_approval}
        testId="repo-health-footer-firstpass-value"
        tdTestId="repo-health-footer-firstpass"
        className="py-2"
      />
    </tr>
  );
}

interface RepoHealthStatus {
  readonly message: string;
  readonly testId: string;
}

function repoHealthStatus(loaded: boolean, error: boolean, isEmpty: boolean): RepoHealthStatus | null {
  if (!loaded) {
    return { message: "Loading repository health…", testId: "repo-health-table-loading" };
  }
  if (error) {
    return { message: "Unable to load repository health data.", testId: "repo-health-table-error" };
  }
  if (isEmpty) {
    return { message: "No repositories to compare yet.", testId: "repo-health-table-empty" };
  }
  return null;
}

interface RepoHealthTableBodyProps {
  readonly rows: readonly RepoHealthRow[];
  readonly blockedCount: number;
  readonly fleet: HealthScopeWindows | null;
  readonly qualityMetrics: QualityMetrics | null;
}

function RepoHealthTableBody({
  rows,
  blockedCount,
  fleet,
  qualityMetrics,
}: Readonly<RepoHealthTableBodyProps>) {
  return (
    <>
      <div
        className="relative overflow-x-auto"
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable so a keyboard-only user can scroll this table's columns past the fold (axe scrollable-region-focusable)
        tabIndex={0}
        aria-label="Repository health table, scroll horizontally for more columns"
      >
        <table data-testid="repo-health-table" className="w-full text-left text-sm">
          <RepoHealthTableHead />
          <tbody>
            {rows.map((row) => (
              <RepoHealthDataRow key={row.repo} row={row} />
            ))}
            {fleet !== null && (
              <FleetFooterRow
                blockedCount={blockedCount}
                fleet={fleet}
                qualityMetrics={qualityMetrics}
              />
            )}
          </tbody>
        </table>
      </div>
      <RepoHealthLegend />
    </>
  );
}

export interface RepoHealthTableProps {
  readonly rows: readonly RepoHealthRow[];
  readonly computedAt: string | null;
  readonly blockedCount: number;
  readonly fleet: HealthScopeWindows | null;
  readonly qualityMetrics: QualityMetrics | null;
  readonly loaded?: boolean;
  readonly error?: boolean;
  readonly digestRecovery?: RecoverySnapshot | null;
  readonly digestResidue?: ResidueItem[] | null;
  readonly digestOverdue?: boolean | null;
}

export default function RepoHealthTable({
  rows,
  computedAt,
  blockedCount,
  fleet,
  qualityMetrics,
  loaded = true,
  error = false,
  digestRecovery = null,
  digestResidue = null,
  digestOverdue = null,
}: Readonly<RepoHealthTableProps>) {
  const ordered = orderedHealthRepoRows(rows);
  const status = repoHealthStatus(loaded, error, ordered.length === 0);
  return (
    <div
      data-testid="repo-health-panel"
      className="mb-4 rounded-lg border border-border bg-surface-elevated px-4 py-3"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        <h2 className="text-sm font-medium text-text-primary">Repository health</h2>
        {computedAt !== null && <FreshnessIndicator iso={computedAt} testId="repo-health-freshness" />}
      </div>
      {status !== null ? (
        <p className="text-sm text-text-muted" data-testid={status.testId}>
          {status.message}
        </p>
      ) : (
        <RepoHealthTableBody
          rows={ordered}
          blockedCount={blockedCount}
          fleet={fleet}
          qualityMetrics={qualityMetrics}
        />
      )}
      <DigestSignalSection
        recovery={digestRecovery}
        residue={digestResidue}
        digestOverdue={digestOverdue}
      />
    </div>
  );
}
