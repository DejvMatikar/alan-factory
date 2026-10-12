import { expect, test } from "./fixtures/test-base";
import { stubHealthRepos } from "./fixtures/healthWindows";

const DEFAULT_REPO_KEY = "DejvMatikar/alan";

test(
  "/health renders the repository comparison table, not the metrics panel, when unscoped",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health");
    await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible();
    await expect(page.getByTestId("repo-health-panel")).toBeVisible();
    await expect(page.getByTestId("health-metrics-panel")).toHaveCount(0);
  },
);

test(
  "a repository row's name cell is a genuine link, never a whole-row click target",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health");
    const panel = page.getByTestId("repo-health-panel");
    await expect(panel).toBeVisible();
    const row = page.getByTestId(`repo-health-row-${DEFAULT_REPO_KEY}`);
    if ((await row.count()) === 0) {
      test.skip(true, "no repository rows in this environment");
    }
    const link = page.getByTestId(`repo-health-link-${DEFAULT_REPO_KEY}`);
    await expect(link).toHaveAttribute(
      "href",
      `/health?engine=${encodeURIComponent(DEFAULT_REPO_KEY)}`,
    );
    await link.focus();
    await expect(link).toBeFocused();
  },
);

test(
  "?engine= scope renders the repository-scoped metrics table, not the comparison table",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_REPO_KEY)}`);
    await expect(page.getByTestId("health-scope-indicator")).toContainText(DEFAULT_REPO_KEY);
    await expect(page.getByTestId("health-metrics-panel")).toBeVisible();
    await expect(page.getByTestId("repo-health-panel")).toHaveCount(0);
    await expect(page.getByTestId("repo-metrics-table")).toBeVisible();
    await expect(page.getByTestId("repo-metric-row-build_success")).toBeVisible();
  },
);

test(
  "scoped view's Show all repositories link returns to the comparison table",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_REPO_KEY)}`);
    await page.getByRole("link", { name: "Show all repositories" }).click();
    await expect(page).toHaveURL((u) => new URL(u).search === "");
    await expect(page.getByTestId("repo-health-panel")).toBeVisible();
  },
);

test(
  "the comparison table shows 30-day values with verdict symbols, including the All repositories row, and a legend",
  { tag: "@no-claude" },
  async ({ page }) => {
    await stubHealthRepos(page, DEFAULT_REPO_KEY);
    await page.goto("/health");
    await expect(page.getByTestId(`repo-health-cell-outcome-${DEFAULT_REPO_KEY}`)).toHaveText("60%");
    await expect(page.getByTestId(`repo-health-cell-outcome-${DEFAULT_REPO_KEY}-symbol`)).toHaveText("▲");
    await expect(page.getByTestId(`repo-health-cell-autonomous-${DEFAULT_REPO_KEY}-symbol`)).toHaveText("▼");
    await expect(page.getByTestId("repo-health-footer-outcome-value")).toHaveText("60%");
    await expect(page.getByTestId("repo-health-footer-outcome-value-symbol")).toHaveText("▲");
    await expect(page.getByTestId("repo-health-footer-build-value-symbol")).toHaveText("≈");
    await expect(page.getByTestId("repo-health-footer-firstpass-value-sample")).toHaveCount(0);
    const legend = page.getByTestId("repo-health-legend");
    await expect(legend).toBeVisible();
    await expect(legend).toContainText("Rates cover the last 30 days");
    await expect(legend).toContainText("within ±5 percentage points");
  },
);
