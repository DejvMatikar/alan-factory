import type { Page } from "@playwright/test";

interface Reading {
  value: number | null;
  sample: number;
  covered_days: number;
}

interface Metric {
  d7: Reading;
  d30: Reading;
  verdict: "better" | "worse" | "same" | "insufficient";
  higher_is_better: boolean;
}

const EMPTY_WINDOW: Reading = { value: null, sample: 0, covered_days: 30 };

function metric(
  d7: Reading,
  d30: Reading,
  verdict: Metric["verdict"],
  higherIsBetter = true,
): Metric {
  return { d7, d30, verdict, higher_is_better: higherIsBetter };
}

function reading(value: number, sample: number, coveredDays = 30): Reading {
  return { value, sample, covered_days: coveredDays };
}

const FUNNEL_7D = {
  dispatched: 30,
  not_crashed: 29,
  not_needed_human: 27,
  converged: 25,
  merged_pr: 24,
  dod_evidenced: 21,
  autonomous: 18,
};

const FUNNEL_30D = {
  dispatched: 90,
  not_crashed: 86,
  not_needed_human: 80,
  converged: 74,
  merged_pr: 70,
  dod_evidenced: 55,
  autonomous: 40,
};

function scope() {
  return {
    outcome: metric(reading(0.9, 30), reading(0.6, 90), "better"),
    autonomous: metric(reading(0.4, 30), reading(0.6, 90), "worse"),
    build_success: metric(reading(0.7, 30), reading(0.7, 90), "same"),
    first_pass_approval: metric(reading(0.5, 4), reading(0.6, 60, 12), "insufficient"),
    unproven_clauses: metric(reading(0.1, 50), reading(0.3, 100), "better", false),
    format_error: metric(EMPTY_WINDOW, EMPTY_WINDOW, "insufficient", false),
    correction_recovery: metric(EMPTY_WINDOW, EMPTY_WINDOW, "insufficient"),
    rescue_success: metric(reading(1, 3), reading(0.8, 10), "insufficient"),
    rescue_load: metric(reading(0.2, 40), reading(0.25, 120), "same"),
    funnel: { d7: FUNNEL_7D, d30: FUNNEL_30D },
  };
}

export const STUBBED_IN_FLIGHT = 3;

function stubbedHealthRepos(repo: string) {
  return {
    computed_at: null,
    metrics_computed_at: new Date().toISOString(),
    unattributed: 2,
    fleet: scope(),
    rows: [
      {
        repo,
        outstanding_findings: { value: 0, sample: 0, low_data: false },
        in_flight: { value: STUBBED_IN_FLIGHT, sample: STUBBED_IN_FLIGHT, low_data: false },
        windows: scope(),
      },
    ],
  };
}

export async function stubHealthRepos(page: Page, repo: string): Promise<void> {
  await page.route("**/api/health/repos", async (route) => {
    await route.fulfill({ json: stubbedHealthRepos(repo) });
  });
}
