import type {
  FinisherFunnelCounts,
  HealthMetricWindows,
  HealthScopeWindows,
  HealthVerdict,
  HealthWindow,
  RepoHealthResponse,
  RepoHealthRow,
} from "../api";

export const METRICS_COMPUTED_AT = "2026-09-24T10:00:00Z";

export const ZERO_FUNNEL: FinisherFunnelCounts = {
  dispatched: 0,
  not_crashed: 0,
  not_needed_human: 0,
  converged: 0,
  merged_pr: 0,
  dod_evidenced: 0,
  autonomous: 0,
};

export function healthWindow(
  value: number | null,
  sample: number,
  coveredDays = 30,
): HealthWindow {
  return { value, sample, covered_days: coveredDays };
}

export function metricWindows(
  d7: HealthWindow,
  d30: HealthWindow,
  verdict: HealthVerdict = "insufficient",
  higherIsBetter = true,
): HealthMetricWindows {
  return { d7, d30, verdict, higher_is_better: higherIsBetter };
}

export function emptyMetric(higherIsBetter = true): HealthMetricWindows {
  return metricWindows(healthWindow(null, 0, 7), healthWindow(null, 0), "insufficient", higherIsBetter);
}

export function scopeWindows(overrides: Partial<HealthScopeWindows> = {}): HealthScopeWindows {
  return {
    outcome: emptyMetric(),
    autonomous: emptyMetric(),
    build_success: emptyMetric(),
    first_pass_approval: emptyMetric(),
    unproven_clauses: emptyMetric(false),
    format_error: emptyMetric(false),
    correction_recovery: emptyMetric(),
    rescue_success: emptyMetric(),
    rescue_load: emptyMetric(),
    funnel: { d7: ZERO_FUNNEL, d30: ZERO_FUNNEL },
    ...overrides,
  };
}

export function repoRow(repo: string, overrides: Partial<RepoHealthRow> = {}): RepoHealthRow {
  return {
    repo,
    outstanding_findings: { value: 0, sample: 0, low_data: false },
    in_flight: { value: 0, sample: 0, low_data: false },
    windows: scopeWindows(),
    ...overrides,
  };
}

export function repoHealthResponse(
  overrides: Partial<RepoHealthResponse> = {},
): RepoHealthResponse {
  return {
    computed_at: null,
    metrics_computed_at: METRICS_COMPUTED_AT,
    unattributed: 0,
    fleet: scopeWindows(),
    rows: [],
    ...overrides,
  };
}
