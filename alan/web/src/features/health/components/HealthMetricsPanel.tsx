import { useId } from "react";

import type { HealthScopeWindows, QualityMetrics, RepoHealthRow } from "../../../api";
import { formatRelativeTime } from "../../../lib/format";
import { plural } from "../../../lib/unblockerSummaryFields";
import { useHealthContext } from "../../../api/HealthProvider";
import Disclosure from "../../../ui/Disclosure";
import { CAVEATS } from "./repoMetricRows";
import RepoMetricsTable from "./RepoMetricsTable";

export function FreshnessIndicator({ iso, testId }: Readonly<{ iso: string; testId: string }>) {
  const descId = useId();
  const parsed = new Date(iso);
  const absolute = Number.isNaN(parsed.getTime()) ? "Unknown" : parsed.toLocaleString();
  return (
    <span
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable to expose the aria-describedby timestamp to screen readers; not a control
      tabIndex={0}
      title={absolute}
      aria-describedby={descId}
      data-testid={testId}
      className="text-xs text-text-muted focus:outline-none focus:ring-2 focus:ring-text-primary/60"
    >
      Updated {formatRelativeTime(iso)}
      <span id={descId} className="sr-only">
        {absolute}
      </span>
    </span>
  );
}

export interface MetricCellProps {
  label: string;
  value: string;
  valueTestid: string;
  explainer: string;
  sampleText?: string | undefined;
  sampleTestid?: string;
  lowData?: boolean;
}

export function MetricCell({
  label,
  value,
  valueTestid,
  explainer,
  sampleText,
  sampleTestid,
  lowData,
}: Readonly<MetricCellProps>) {
  const explainerId = useId();
  const valueClass =
    lowData === true
      ? "text-sm text-text-muted tabular-nums focus:outline-none focus:ring-2 focus:ring-text-primary/60"
      : "text-2xl font-semibold tabular-nums text-text-primary focus:outline-none focus:ring-2 focus:ring-text-primary/60";
  return (
    <div className="flex flex-col gap-0.5">
      <span
        data-testid={valueTestid}
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable to expose the aria-describedby explainer to screen readers; not a control
        tabIndex={0}
        aria-describedby={explainerId}
        className={valueClass}
      >
        {value}
      </span>
      {sampleText !== undefined && (
        <span data-testid={sampleTestid} className="text-xs text-text-muted">
          {sampleText}
        </span>
      )}
      <span className="text-xs text-text-muted">{label}</span>
      <p id={explainerId} className="max-w-[16rem] text-xs text-text-muted">
        {explainer}
      </p>
    </div>
  );
}

function FleetFallbackNotice({ repo }: Readonly<{ repo: string }>) {
  return (
    <p data-testid="health-metrics-fleet-fallback" className="mb-3 text-xs text-text-muted">
      No repository-specific data yet for {repo} — figures below are fleet-wide.
    </p>
  );
}

function InFlightLine({ count }: Readonly<{ count: number | null }>) {
  return (
    <p data-testid="health-metrics-in-flight" className="mb-3 text-sm text-text-primary">
      Right now: {count ?? "—"} in flight
    </p>
  );
}

function AttributionNote({
  repo,
  unattributed,
}: Readonly<{ repo: string; unattributed: number }>) {
  return (
    <p data-testid="health-metrics-attribution-note" className="mt-2 text-xs text-text-muted">
      Format error and correction recovery for {repo} count only records that can be attributed to
      a repository.
      {unattributed > 0 &&
        ` ${plural(unattributed, "record")} in the last 30 days could not be attributed and appear only in the fleet-wide figures.`}
    </p>
  );
}

function Caveats() {
  return (
    <Disclosure
      preset="panel"
      defaultOpen={false}
      testId="health-metrics-caveats"
      label="What these numbers mean"
    >
      <div className="mt-2">
        <dl className="grid gap-2 text-xs text-text-muted">
          {CAVEATS.map(({ id, title, text }) => (
            <div key={id} data-testid={`health-metrics-caveat-${id}`}>
              <dt className="font-medium text-text-primary">{title}</dt>
              <dd>{text}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Disclosure>
  );
}

function PanelStatus({ failed }: Readonly<{ failed: boolean }>) {
  return failed ? (
    <p data-testid="health-metrics-error" className="text-sm text-text-muted">
      Unable to load health metrics.
    </p>
  ) : (
    <p data-testid="health-metrics-loading" className="text-sm text-text-muted">
      Loading health metrics…
    </p>
  );
}

interface MetricsBodyProps {
  readonly repo: string;
  readonly scoped: boolean;
  readonly windows: HealthScopeWindows;
  readonly inFlight: number | null;
  readonly unattributed: number;
}

function MetricsBody({ repo, scoped, windows, inFlight, unattributed }: Readonly<MetricsBodyProps>) {
  return (
    <>
      {!scoped && <FleetFallbackNotice repo={repo} />}
      <InFlightLine count={inFlight} />
      <RepoMetricsTable windows={windows} />
      {scoped && <AttributionNote repo={repo} unattributed={unattributed} />}
      <div className="mt-3">
        <Caveats />
      </div>
    </>
  );
}

export interface HealthMetricsPanelProps {
  readonly repo: string;
  readonly repoRow: RepoHealthRow | null;
  readonly fleet: HealthScopeWindows | null;
  readonly metricsComputedAt: string | null;
  readonly unattributed: number;
  readonly loaded: boolean;
  readonly error: boolean;
}

interface ScopeReading {
  readonly scoped: boolean;
  readonly windows: HealthScopeWindows | null;
  readonly inFlight: number | null;
}

function readScope(
  repoRow: RepoHealthRow | null,
  fleet: HealthScopeWindows | null,
  qualityMetrics: QualityMetrics | null,
): ScopeReading {
  if (repoRow !== null) {
    return { scoped: true, windows: repoRow.windows, inFlight: repoRow.in_flight.value };
  }
  return {
    scoped: false,
    windows: fleet,
    inFlight: qualityMetrics?.in_flight_build_count ?? null,
  };
}

export default function HealthMetricsPanel({
  repo,
  repoRow,
  fleet,
  metricsComputedAt,
  unattributed,
  loaded,
  error,
}: Readonly<HealthMetricsPanelProps>) {
  const { qualityMetrics } = useHealthContext();
  const { scoped, windows, inFlight } = readScope(repoRow, fleet, qualityMetrics);
  return (
    <div
      data-testid="health-metrics-panel"
      className="mb-4 rounded-lg border border-border bg-surface-elevated px-4 py-3"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        <h2 className="text-sm font-medium text-text-primary">Health metrics</h2>
        {metricsComputedAt !== null && (
          <FreshnessIndicator iso={metricsComputedAt} testId="health-freshness" />
        )}
      </div>
      {error || windows === null ? (
        <PanelStatus failed={error || loaded} />
      ) : (
        <MetricsBody
          repo={repo}
          scoped={scoped}
          windows={windows}
          inFlight={inFlight}
          unattributed={unattributed}
        />
      )}
    </div>
  );
}
