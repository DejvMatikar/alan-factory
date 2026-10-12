import { useMemo, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { HealthFinding } from "../../../api";
import HealthMetricsPanel from "../components/HealthMetricsPanel";
import { useHealthContext } from "../../../api/HealthProvider";
import { useEngines } from "../../../hooks/useEngines";
import { useRepoHealthMetrics } from "../hooks/useRepoHealthMetrics";
import DriverLoopsPanel from "../components/DriverLoopsPanel";
import FindingsTable from "../components/FindingsTable";
import RecurringProcessesPanel from "../components/RecurringProcessesPanel";
import RepoHealthTable from "../components/RepoHealthTable";
import UnblockerActivityPanel from "../components/UnblockerActivityPanel";
import { useHealthDigest } from "../hooks/useHealthDigest";
import { engineKeyMatchesRepo } from "../../../lib/fold";
import { oldestTimestamp } from "../../../lib/metricWindow";
import Disclosure from "../../../ui/Disclosure";

function EmptyState({ verdict }: Readonly<{ verdict: string }>) {
  return (
    <div data-testid="health-view-empty" className="text-text-muted">
      {verdict === "healthy" ? "All systems healthy." : "No findings to display."}
    </div>
  );
}

function StaleNotice() {
  return (
    <div
      data-testid="health-view-stale"
      className="mb-4 rounded border border-border bg-surface-elevated p-3 text-sm text-text-muted"
    >
      Health data is stale or unavailable. Run a triage sweep to refresh.
    </div>
  );
}

function HealthViewHeader() {
  return (
    <div className="mb-4">
      <h2 className="text-xl font-semibold">System Health</h2>
    </div>
  );
}

function visibleFindingsOf(
  healthState: { findings: HealthFinding[] } | null,
  hiddenEngineKeys: ReadonlySet<string>,
  scopeEngine: string | null,
  defaultKey: string | null,
): HealthFinding[] {
  if (healthState === null) return [];
  return healthState.findings.filter(
    (f) =>
      (f.engine_key == null || !hiddenEngineKeys.has(f.engine_key)) &&
      (scopeEngine === null || engineKeyMatchesRepo(f.engine_key, scopeEngine, defaultKey)),
  );
}

function FindingsDisclosure({
  count,
  children,
}: Readonly<{ count: number; children: ReactNode }>) {
  return (
    <Disclosure
      preset="panel"
      defaultOpen={false}
      testId="health-findings-disclosure"
      label={`Other health findings (${count})`}
    >
      <div className="mt-2">{children}</div>
    </Disclosure>
  );
}

function ScopeIndicator({ engine }: Readonly<{ engine: string }>) {
  return (
    <p data-testid="health-scope-indicator" className="mb-4 text-sm text-text-muted">
      Activity and findings below are scoped to {engine}.{" "}
      <Link to="/health" className="text-btn-action underline">
        Show all repositories
      </Link>
    </p>
  );
}

export default function HealthView() {
  const { healthState, qualityMetrics, blockedCount } = useHealthContext();
  const { hasNonDefaultEngine, hiddenEngineKeys, defaultEngineKey } = useEngines();
  const [searchParams] = useSearchParams();
  const scopeEngine = searchParams.get("engine");
  const {
    rows: repoRows,
    fleet,
    computedAt: repoComputedAt,
    metricsComputedAt,
    unattributed,
    loaded: repoRowsLoaded,
    error: repoRowsError,
  } = useRepoHealthMetrics();
  const {
    recovery: digestRecovery,
    residue: digestResidue,
    digestOverdue,
  } = useHealthDigest(scopeEngine === null);
  const visibleFindings = useMemo(
    () => visibleFindingsOf(healthState, hiddenEngineKeys, scopeEngine, defaultEngineKey),
    [healthState, hiddenEngineKeys, scopeEngine, defaultEngineKey],
  );
  const repoRow = useMemo(
    () => (scopeEngine === null ? null : (repoRows.find((r) => r.repo === scopeEngine) ?? null)),
    [repoRows, scopeEngine],
  );

  return (
    <div data-testid="health-view">
      <HealthViewHeader />
      {scopeEngine !== null && <ScopeIndicator engine={scopeEngine} />}
      {scopeEngine === null ? (
        <>
          <RepoHealthTable
            rows={repoRows}
            computedAt={oldestTimestamp([repoComputedAt, metricsComputedAt])}
            blockedCount={blockedCount}
            fleet={fleet}
            qualityMetrics={qualityMetrics}
            loaded={repoRowsLoaded}
            error={repoRowsError}
            digestRecovery={digestRecovery}
            digestResidue={digestResidue}
            digestOverdue={digestOverdue}
          />
          <DriverLoopsPanel />
        </>
      ) : (
        <>
          <HealthMetricsPanel
            repo={scopeEngine}
            repoRow={repoRow}
            fleet={fleet}
            metricsComputedAt={metricsComputedAt}
            unattributed={unattributed}
            loaded={repoRowsLoaded}
            error={repoRowsError}
          />
          <RecurringProcessesPanel engineKey={scopeEngine} />
        </>
      )}
      <UnblockerActivityPanel engine={scopeEngine} />
      {healthState === null && <StaleNotice />}
      {healthState !== null && visibleFindings.length === 0 && (
        <EmptyState verdict={healthState.verdict} />
      )}
      {healthState !== null && visibleFindings.length > 0 && (
        <FindingsDisclosure count={visibleFindings.length}>
          <FindingsTable findings={visibleFindings} hasNonDefaultEngine={hasNonDefaultEngine} />
        </FindingsDisclosure>
      )}
    </div>
  );
}
