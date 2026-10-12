import { expect, test } from "./fixtures/test-base";
import { STUBBED_IN_FLIGHT, stubHealthRepos } from "./fixtures/healthWindows";

const DEFAULT_REPO_KEY = "DejvMatikar/alan";
const SCOPED_HEALTH_PATH = `/health?engine=${encodeURIComponent(DEFAULT_REPO_KEY)}`;

test(
  "a repo-scoped /health shows one metrics table with the four columns and the three groups",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(SCOPED_HEALTH_PATH);
    await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible();
    await expect(page.getByTestId("health-metrics-panel")).toBeVisible();
    const table = page.getByTestId("repo-metrics-table");
    await expect(table).toBeVisible();
    for (const name of ["Metric", "Last 7 days", "Last 30 days", "7d vs 30d"]) {
      await expect(table.getByRole("columnheader", { name })).toBeVisible();
    }
    for (const group of ["delivery", "quality", "unblocker"]) {
      await expect(page.getByTestId(`repo-metric-group-${group}`)).toBeVisible();
    }
    await expect(page.getByText(/mean fix cycles/i)).toHaveCount(0);
  },
);

test(
  "each metric name carries a persistent one-line explanation linked for screen readers",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(SCOPED_HEALTH_PATH);
    const summary = page.getByTestId("repo-metric-autonomous-summary");
    await expect(summary).toBeVisible();
    const header = page.getByTestId("repo-metric-row-autonomous").getByRole("rowheader");
    await expect(header).toHaveAttribute("aria-describedby", (await summary.getAttribute("id")) ?? "");
  },
);

test(
  "the longer caveats sit in a collapsed What these numbers mean section that opens on demand",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(SCOPED_HEALTH_PATH);
    const disclosure = page.getByTestId("health-metrics-caveats");
    await expect(disclosure).toBeVisible();
    await expect(disclosure).not.toHaveAttribute("open", "");
    await disclosure.getByText("What these numbers mean").click();
    await expect(disclosure).toHaveAttribute("open", "");
    await expect(page.getByTestId("health-metrics-caveat-autonomous")).toContainText(
      "upper bound on autonomy",
    );
  },
);

test(
  "the metrics area shows verdict symbols with words, dims low data, and says a short window covers fewer days",
  { tag: "@no-claude" },
  async ({ page }) => {
    await stubHealthRepos(page, DEFAULT_REPO_KEY);
    await page.goto(SCOPED_HEALTH_PATH);
    await expect(page.getByTestId("repo-metric-outcome-verdict")).toContainText("▲ Better");
    await expect(page.getByTestId("repo-metric-autonomous-verdict")).toContainText("▼ Worse");
    await expect(page.getByTestId("repo-metric-build_success-verdict")).toContainText("≈ About the same");
    await expect(page.getByTestId("repo-metric-first_pass_approval-verdict")).toContainText(
      "Not enough data to compare",
    );
    const lowData = page.getByTestId("repo-metric-first_pass_approval-7d");
    await expect(lowData).toContainText("n=4");
    await expect(lowData).toContainText("low data");
    await expect(page.getByTestId("repo-metric-first_pass_approval-30d")).toContainText("covers only 12 days");
    await expect(page.getByTestId("repo-metric-format_error-30d")).toContainText("not measured yet");
    await expect(page.getByTestId("repo-metric-funnel-merged_pr-30d")).toContainText("70 of 90");
  },
);

test(
  "the table has one updated line and a right-now in-flight line above it",
  { tag: "@no-claude" },
  async ({ page }) => {
    await stubHealthRepos(page, DEFAULT_REPO_KEY);
    await page.goto(SCOPED_HEALTH_PATH);
    await expect(page.getByTestId("health-metrics-in-flight")).toHaveText(
      `Right now: ${STUBBED_IN_FLIGHT} in flight`,
    );
    const freshness = page.getByTestId("health-freshness");
    await expect(freshness).toHaveCount(1);
    await freshness.focus();
    await expect(freshness).toBeFocused();
    const describedBy = await freshness.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    await expect(page.locator(`[id="${describedBy}"]`)).not.toBeEmpty();
  },
);

test(
  "on a narrow screen the table scrolls inside a keyboard-reachable region and the page does not scroll sideways",
  { tag: "@no-claude" },
  async ({ page }) => {
    await stubHealthRepos(page, DEFAULT_REPO_KEY);
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(SCOPED_HEALTH_PATH);
    const region = page.getByRole("region", { name: /repository metrics table/i });
    await expect(region).toBeVisible();
    await region.focus();
    await expect(region).toBeFocused();
    const overflow = await region.evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(overflow).toBe(true);
    const pageScrollsSideways = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(pageScrollsSideways).toBe(false);
  },
);

test(
  "home page does not contain the health-metrics-panel",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("health-metrics-panel")).toHaveCount(0);
  },
);
