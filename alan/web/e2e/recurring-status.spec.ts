import { expect, test } from "./fixtures/test-base";

const DEFAULT_ENGINE_KEY = "DejvMatikar/alan";

test.describe(
  "recurring-process status panel — read-only, keyboard-operable disclosure",
  { tag: "@no-claude" },
  () => {
    test("is collapsed by default with a plain-text summary, and expands/collapses via keyboard activation", async ({
      page,
    }) => {
      await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_ENGINE_KEY)}`);
      const disclosure = page.getByTestId(`recurring-processes-disclosure-${DEFAULT_ENGINE_KEY}`);
      await expect(disclosure).toBeVisible();
      await expect(disclosure).toHaveJSProperty("open", false);
      await expect(disclosure).toContainText(/\d+ processes?/);

      const triageRow = page.getByTestId("recurring-row-triage_sweep");
      await expect(triageRow).not.toBeVisible();

      const summary = disclosure.locator("summary");
      await summary.focus();
      await expect(summary).toBeFocused();
      await summary.press("Enter");
      await expect(disclosure).toHaveJSProperty("open", true);

      await expect(triageRow).toBeVisible();
      await expect(triageRow).toContainText(/Overdue|On time|Unknown/);
      await expect(triageRow).toContainText(
        /Succeeded|Failed|Needs a person|Ended without completing|Currently running|Queued to run|Never run yet|Outcome unknown|Completed with degradation/,
      );

      const backupRow = page.getByTestId("recurring-row-backup_job");
      await expect(backupRow).toBeVisible();

      const digestRow = triageRow.getByTestId("recurring-row-daily_digest");
      await expect(digestRow).toBeVisible();
      await expect(digestRow).toContainText("Rides on triage_sweep");

      await summary.press(" ");
      await expect(disclosure).toHaveJSProperty("open", false);
      await expect(triageRow).not.toBeVisible();
    });

    test("moves keyboard focus onward past the summary with no focus trap, and exposes no mutating control", async ({
      page,
    }) => {
      await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_ENGINE_KEY)}`);
      const disclosure = page.getByTestId(`recurring-processes-disclosure-${DEFAULT_ENGINE_KEY}`);
      const summary = disclosure.locator("summary");
      await summary.focus();
      await expect(summary).toBeFocused();

      await page.keyboard.press("Tab");
      await expect(summary).not.toBeFocused();

      const panel = page.getByTestId(`recurring-processes-panel-${DEFAULT_ENGINE_KEY}`);
      await expect(panel.locator("button")).toHaveCount(0);
      await expect(panel.locator('[role="button"]')).toHaveCount(0);
    });

    test("hides backup coverage while collapsed and reveals it alongside the backup entry once expanded", async ({
      page,
    }) => {
      await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_ENGINE_KEY)}`);
      const coverage = page.getByTestId(`recurring-backup-coverage-${DEFAULT_ENGINE_KEY}`);
      await expect(coverage).toBeAttached();
      await expect(coverage).not.toBeVisible();

      const disclosure = page.getByTestId(`recurring-processes-disclosure-${DEFAULT_ENGINE_KEY}`);
      await disclosure.locator("summary").click();
      await expect(coverage).toBeVisible();
    });

    test("internal sweep links navigate in-app, and the backup row states its reason without a link", async ({
      page,
    }) => {
      await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_ENGINE_KEY)}`);
      const disclosure = page.getByTestId(`recurring-processes-disclosure-${DEFAULT_ENGINE_KEY}`);
      const summary = disclosure.locator("summary");
      await summary.click();
      await expect(disclosure).toHaveJSProperty("open", true);

      const triageRow = page.getByTestId("recurring-row-triage_sweep");
      const triageLink = triageRow.getByRole("link", { name: /Failure triage sweep/ });
      await expect(triageLink).toHaveAttribute("href", "/health");
      await triageLink.click();
      await expect(page).toHaveURL(/\/health$/);

      await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_ENGINE_KEY)}`);
      await summary.click();
      await expect(disclosure).toHaveJSProperty("open", true);
      const digestRow = page.getByTestId("recurring-row-daily_digest");
      const digestLink = digestRow.getByRole("link", { name: /Daily digest/ });
      await expect(digestLink).toHaveAttribute("href", "/health");
      await digestLink.click();
      await expect(page).toHaveURL(/\/health$/);

      await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_ENGINE_KEY)}`);
      await summary.click();
      await expect(disclosure).toHaveJSProperty("open", true);
      const backupRow = page.getByTestId("recurring-row-backup_job");
      await expect(backupRow.getByRole("link")).toHaveCount(0);
      await expect(backupRow).toContainText("No viewing surface in the product");
    });

    test("the triage sweep's process link is keyboard-reachable and Enter activates it", async ({
      page,
    }) => {
      await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_ENGINE_KEY)}`);
      const disclosure = page.getByTestId(`recurring-processes-disclosure-${DEFAULT_ENGINE_KEY}`);
      const summary = disclosure.locator("summary");
      await summary.focus();
      await summary.press("Enter");
      await expect(disclosure).toHaveJSProperty("open", true);

      const triageLink = page
        .getByTestId("recurring-row-triage_sweep")
        .getByRole("link", { name: /Failure triage sweep/ });
      await triageLink.focus();
      await expect(triageLink).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/\/health$/);
    });
  },
);
