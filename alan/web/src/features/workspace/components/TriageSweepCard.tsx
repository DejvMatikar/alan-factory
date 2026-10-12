import type { Sweeper, SweeperAction } from "../../../api";
import { formatRelativeTime } from "../../../lib/format";
import { useHealthContext } from "../../../api/HealthProvider";

const _STALE_AFTER_MS = 60 * 60 * 1000;

const _EMPTY_SWEEPER: Sweeper = {
  timestamp_utc: null,
  surface_count: null,
  blocked_count: null,
  next_eligible_utc: null,
  recent_actions: [],
};

function _isStale(timestampUtc: string): boolean {
  return Date.now() - new Date(timestampUtc).getTime() > _STALE_AFTER_MS;
}

function _countOrDash(count: number | null): string {
  return count === null ? "—" : String(count);
}

function SweepFreshness({ timestampUtc }: Readonly<{ timestampUtc: string }>) {
  const stale = _isStale(timestampUtc);
  return (
    <div className="flex items-center gap-2">
      <span
        title={new Date(timestampUtc).toLocaleString()}
        data-testid="triage-sweep-freshness"
        className="text-xs text-text-muted"
      >
        Swept {formatRelativeTime(timestampUtc)}
      </span>
      {stale && (
        <span
          data-testid="triage-sweep-stale"
          className="rounded bg-amber-500/15 px-1.5 py-0.5 text-xs font-medium text-amber-300"
        >
          Stale
        </span>
      )}
    </div>
  );
}

interface SweepCountProps {
  value: number | null;
  label: string;
  testId: string;
}

function SweepCount({ value, label, testId }: Readonly<SweepCountProps>) {
  return (
    <div className="flex flex-col gap-0.5">
      <span data-testid={testId} className="text-2xl font-semibold tabular-nums text-text-primary">
        {_countOrDash(value)}
      </span>
      <span className="text-xs text-text-muted">{label}</span>
    </div>
  );
}

function NextEligible({ nextEligibleUtc }: Readonly<{ nextEligibleUtc: string | null }>) {
  if (nextEligibleUtc === null) return null;
  return (
    <p data-testid="triage-sweep-next-eligible" className="mt-3 text-xs text-text-muted">
      Next eligible: {new Date(nextEligibleUtc).toLocaleString()}
    </p>
  );
}

function _actionLabel(action: string): string {
  return action.replaceAll("_", " ");
}

function RecentActionRow({ entry }: Readonly<{ entry: SweeperAction }>) {
  return (
    <li className="flex items-center justify-between gap-2 text-xs text-text-muted">
      <span className="text-text-primary">{_actionLabel(entry.action)}</span>
      <span title={new Date(entry.timestamp_utc).toLocaleString()}>
        {formatRelativeTime(entry.timestamp_utc)}
      </span>
    </li>
  );
}

function RecentActions({ actions }: Readonly<{ actions: readonly SweeperAction[] }>) {
  return (
    <div className="mt-3 border-t border-border pt-3">
      <h4 className="text-xs font-medium text-text-muted">Recent activity</h4>
      {actions.length === 0 ? (
        <p data-testid="triage-sweep-actions-empty" className="mt-1 text-xs text-text-muted">
          No recent activity
        </p>
      ) : (
        <ul data-testid="triage-sweep-actions" className="mt-1 space-y-1">
          {actions.map((entry, i) => (
            <RecentActionRow key={`${entry.action}-${entry.timestamp_utc}-${i}`} entry={entry} />
          ))}
        </ul>
      )}
    </div>
  );
}

export default function TriageSweepCard() {
  const { sweeper } = useHealthContext();
  const {
    timestamp_utc: timestampUtc,
    surface_count: surfaceCount,
    blocked_count: blockedCount,
    next_eligible_utc: nextEligibleUtc,
    recent_actions: recentActions,
  } = sweeper ?? _EMPTY_SWEEPER;
  const neverRun = timestampUtc === null;

  return (
    <div
      data-testid="triage-sweep-card"
      className="rounded-lg border border-border bg-surface-elevated px-4 py-3"
    >
      <div className="mb-3 flex items-center justify-between gap-3 border-b border-border pb-3">
        <h3 className="text-sm font-medium">Sweeper</h3>
        {neverRun ? (
          <span data-testid="triage-sweep-never-run" className="text-xs text-text-muted">
            Never run
          </span>
        ) : (
          <SweepFreshness timestampUtc={timestampUtc} />
        )}
      </div>
      <div className="flex gap-8">
        <SweepCount
          value={surfaceCount}
          label="Surfaces scanned"
          testId="triage-sweep-surface-count"
        />
        <SweepCount
          value={blockedCount}
          label="Blocked"
          testId="triage-sweep-blocked-count"
        />
      </div>
      <NextEligible nextEligibleUtc={nextEligibleUtc} />
      <RecentActions actions={recentActions} />
    </div>
  );
}
