import type { Page } from "@playwright/test";

import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const DEFAULT_REPO_KEY = "DejvMatikar/alan";
const HEALTH_HREF = `/health?engine=${encodeURIComponent(DEFAULT_REPO_KEY)}`;
const UNBLOCKER_ACTIVITY_URL = /\/api\/health\/unblocker-activity/;

const QUIET_ACTIVITY = {
  scope: "repo",
  repo: DEFAULT_REPO_KEY,
  window_days: 30,
  events_total: 0,
  workspaces_touched: 0,
  waited_workspaces: 0,
  diagnosed_workspaces: 0,
  diagnosable_workspaces: 0,
  still_stuck_workspaces: 0,
  gave_up_workspaces: 0,
  aggregates: [],
  fleet_ever_recorded: true,
  events: [],
  workspaces: [],
};

const BUSY_ACTIVITY = {
  ...QUIET_ACTIVITY,
  events_total: 9,
  workspaces_touched: 4,
  still_stuck_workspaces: 2,
  gave_up_workspaces: 1,
  aggregates: [
    {
      shape: "merge_park",
      total: 9,
      workspaces: 4,
      cleared_workspaces: 1,
      still_stuck_workspaces: 2,
      gone_workspaces: 1,
      retried_workspaces: 2,
      attempt_outcomes: [{ outcome: "failed", count: 3 }],
      report_outcomes: [{ outcome: "needs_human", count: 1 }],
    },
  ],
};

interface VerticalRect {
  readonly top: number;
  readonly bottom: number;
}

const byTestId = (testId: string): string => `[data-testid="${testId}"]`;

async function verticalRectsInOneFrame<Name extends string>(
  page: Page,
  selectors: Readonly<Record<Name, string>>,
): Promise<Record<Name, VerticalRect>> {
  const rects = await page.evaluate(
    (all) =>
      Object.fromEntries(
        Object.entries(all).map(([name, selector]) => {
          const element = document.querySelector(selector);
          if (element === null) throw new Error(`no element matches ${selector}`);
          const { top, bottom } = element.getBoundingClientRect();
          return [name, { top, bottom }];
        }),
      ),
    selectors as Record<string, string>,
  );
  return rects as Record<Name, VerticalRect>;
}

async function stubUnblockerActivity(page: Page, payload: Record<string, unknown>): Promise<void> {
  await page.route(UNBLOCKER_ACTIVITY_URL, async (route) => {
    await route.fulfill({ json: payload });
  });
}

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-repo-dashboard-density-co");
});

test(
  "the repository dashboard delivers all nine density changes in one pass",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);

    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    const railHeading = page.getByTestId("rail-heading");
    const railStartControls = page.getByTestId(`rail-start-controls-${DEFAULT_REPO_KEY}`);
    const railScroll = page.getByTestId("conversation-rail-scroll");
    const railCollapse = page.getByTestId("rail-collapse");
    for (const railPart of [rail, railHeading, railStartControls, railScroll, railCollapse]) {
      await expect(railPart).toBeVisible();
    }
    const railRects = await verticalRectsInOneFrame(page, {
      heading: byTestId("rail-heading"),
      start: byTestId(`rail-start-controls-${DEFAULT_REPO_KEY}`),
      scroll: byTestId("conversation-rail-scroll"),
      collapse: byTestId("rail-collapse"),
    });
    expect(railRects.heading.top).toBeLessThan(railRects.start.top);
    expect(railRects.start.top).toBeLessThan(railRects.scroll.top);
    expect(railRects.collapse.top).toBeGreaterThanOrEqual(railRects.scroll.bottom);

    const buildButton = page.getByTestId(`rail-start-build-${DEFAULT_REPO_KEY}`);
    await expect(buildButton).toHaveAccessibleName(/shortcut Control Shift X/);
    const exploreButton = page.getByTestId(`rail-start-explore-${DEFAULT_REPO_KEY}`);
    await expect(exploreButton).toHaveAccessibleName(/shortcut Control Shift E/);
    await expect(page.getByText(/Press ⌘\/Ctrl\+Enter/)).toHaveCount(0);

    const status = page.getByTestId(`repo-status-strip-${DEFAULT_REPO_KEY}`);
    await expect(status).toBeVisible();
    const statusFieldset = status.locator("fieldset");
    await expect(statusFieldset).not.toHaveAttribute("tabindex");
    const statusRows = statusFieldset.locator(":scope > div");
    await expect(statusRows).toHaveCount(3);
    const statusScrollWidth = await statusFieldset.evaluate((el) => el.scrollWidth);
    const statusClientWidth = await statusFieldset.evaluate((el) => el.clientWidth);
    expect(statusScrollWidth).toBeLessThanOrEqual(statusClientWidth + 1);

    const section = page.getByTestId(`repo-section-${DEFAULT_REPO_KEY}`);
    const heading = section.getByRole("heading", { level: 1 });
    await expect(heading).toBeVisible();
    await expect(heading).toHaveText("Alan");

    const sectionClass = await section.getAttribute("class");
    expect(sectionClass).not.toContain("rounded-lg");
    expect(sectionClass).not.toContain("bg-surface-elevated");
    const sectionStyle = await section.evaluate((el) => {
      const cs = window.getComputedStyle(el);
      return { border: cs.borderWidth, padding: cs.padding };
    });
    expect(sectionStyle.border).toBe("0px");

    const stickyWrapper = heading.locator("xpath=ancestor::div[contains(@class,'sm:sticky')][1]");
    await expect(stickyWrapper).toHaveCount(1);
    const stickyPosition = await stickyWrapper.evaluate((el) => window.getComputedStyle(el).position);
    expect(stickyPosition).toBe("sticky");
    const maintenance = page.getByTestId(`repo-action-maintenance-${DEFAULT_REPO_KEY}`);
    const health = page.getByTestId(`repo-health-${DEFAULT_REPO_KEY}`);
    const hide = page.getByTestId(`repo-action-hide-${DEFAULT_REPO_KEY}`);
    const settings = page.getByTestId(`repo-action-settings-${DEFAULT_REPO_KEY}`);
    const maintenanceBox = await maintenance.boundingBox();
    const healthBox = await health.boundingBox();
    const hideBox = await hide.boundingBox();
    const settingsBox = await settings.boundingBox();
    expect(maintenanceBox!.x).toBeLessThan(hideBox!.x);
    expect(hideBox!.x).toBeLessThan(healthBox!.x);
    expect(healthBox!.x).toBeLessThan(settingsBox!.x);

    const maintenanceDescribedBy = await maintenance.getAttribute("aria-describedby");
    const maintenanceDescription = page.locator(`[id="${String(maintenanceDescribedBy)}"]`);
    await expect(maintenanceDescription).toHaveClass(/sr-only/);
    await maintenance.hover();
    await expect(maintenanceDescription).not.toHaveClass(/sr-only/);
    await page.mouse.move(0, 0);

    const recurringTotal = page.getByTestId(`repo-status-group-${DEFAULT_REPO_KEY}-recurring-total`);
    await expect(recurringTotal).toBeVisible();
    await expect(recurringTotal).toContainText("Recurring");
    await expect(recurringTotal).toHaveAttribute("href", `/health?engine=${encodeURIComponent(DEFAULT_REPO_KEY)}`);

    const workspaceContentSelector = 'table, [data-testid^="repo-empty-"]';
    await expect(page.getByRole("tablist")).toBeVisible();
    await expect(section.locator(workspaceContentSelector)).toBeVisible();
    const sectionSelector = byTestId(`repo-section-${DEFAULT_REPO_KEY}`);
    const orderRects = await verticalRectsInOneFrame(page, {
      status: byTestId(`repo-status-strip-${DEFAULT_REPO_KEY}`),
      tablist: '[role="tablist"]',
      workspaceContent: `${sectionSelector} :is(${workspaceContentSelector})`,
    });
    expect(orderRects.status.top).toBeLessThan(orderRects.tablist.top);
    expect(orderRects.tablist.top).toBeLessThan(orderRects.workspaceContent.top);
  },
);

test.describe("repository status strip — unblocker summary", { tag: "@no-claude" }, () => {
  test("shows events, workspaces touched, still stuck and gave up beside the recurring summary in its own row", async ({
    page,
  }) => {
    await stubUnblockerActivity(page, BUSY_ACTIVITY);
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const strip = page.getByTestId(`repo-status-strip-${DEFAULT_REPO_KEY}`);
    const row = page.getByTestId(`repo-status-unblocker-${DEFAULT_REPO_KEY}`);
    await expect(row).toBeVisible();
    await expect(page.getByTestId(`aggregate-chip-${DEFAULT_REPO_KEY}-unblocker-events`)).toHaveText(/9/);
    await expect(page.getByTestId(`aggregate-chip-${DEFAULT_REPO_KEY}-unblocker-touched`)).toHaveText(/4/);
    await expect(page.getByTestId(`aggregate-chip-${DEFAULT_REPO_KEY}-unblocker-stuck`)).toHaveText(/2/);
    await expect(page.getByTestId(`aggregate-chip-${DEFAULT_REPO_KEY}-unblocker-gave-up`)).toHaveText(/1/);
    await expect(strip.locator("fieldset > div")).toHaveCount(4);
    await expect(strip.locator("fieldset > div").nth(3)).toHaveAttribute(
      "data-testid",
      `repo-status-unblocker-${DEFAULT_REPO_KEY}`,
    );
    const statusFieldset = strip.locator("fieldset");
    const scrollWidth = await statusFieldset.evaluate((el) => el.scrollWidth);
    const clientWidth = await statusFieldset.evaluate((el) => el.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
  });

  test("renders no unblocker summary, and no row of zeros, when the window is quiet", async ({ page }) => {
    await stubUnblockerActivity(page, QUIET_ACTIVITY);
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const strip = page.getByTestId(`repo-status-strip-${DEFAULT_REPO_KEY}`);
    await expect(strip).toBeVisible();
    await expect(page.getByTestId(`repo-status-group-${DEFAULT_REPO_KEY}-recurring-total`)).toBeVisible();
    await expect(page.getByTestId(`repo-status-unblocker-${DEFAULT_REPO_KEY}`)).toHaveCount(0);
    await expect(strip.locator("fieldset > div")).toHaveCount(3);
  });

  test("omits the two alarming chips when nothing is stuck or given up", async ({ page }) => {
    await stubUnblockerActivity(page, {
      ...BUSY_ACTIVITY,
      still_stuck_workspaces: 0,
      gave_up_workspaces: 0,
    });
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(page.getByTestId(`repo-status-unblocker-${DEFAULT_REPO_KEY}`)).toBeVisible();
    await expect(page.getByTestId(`repo-status-group-${DEFAULT_REPO_KEY}-unblocker-stuck`)).toHaveCount(0);
    await expect(page.getByTestId(`repo-status-group-${DEFAULT_REPO_KEY}-unblocker-gave-up`)).toHaveCount(0);
  });

  test("links every chip to the repository's scoped health page, landing on the collapsed unblocker panel", async ({
    page,
  }) => {
    await stubUnblockerActivity(page, BUSY_ACTIVITY);
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const row = page.getByTestId(`repo-status-unblocker-${DEFAULT_REPO_KEY}`);
    await expect(row).toBeVisible();
    const links = row.getByRole("link");
    await expect(links).toHaveCount(4);
    for (const link of await links.all()) {
      await expect(link).toHaveAttribute("href", HEALTH_HREF);
    }
    await page.getByTestId(`repo-status-group-${DEFAULT_REPO_KEY}-unblocker-stuck`).click();
    await expect(page).toHaveURL(new RegExp(`/health\\?engine=${encodeURIComponent(DEFAULT_REPO_KEY)}$`));
    await expect(page.getByTestId("health-scope-indicator")).toContainText(DEFAULT_REPO_KEY);
    await expect(page.getByTestId("unblocker-activity-disclosure")).toHaveJSProperty("open", false);
    await expect(page.getByTestId("unblocker-aggregates-table")).toBeHidden();
  });

  test("does not hold the strip back while the unblocker activity loads, and shows the row once it lands", async ({
    page,
  }) => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(UNBLOCKER_ACTIVITY_URL, async (route) => {
      await gate;
      await route.fulfill({ json: BUSY_ACTIVITY });
    });
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(page.getByTestId(`repo-status-strip-${DEFAULT_REPO_KEY}`)).toBeVisible();
    await expect(page.getByTestId(`repo-status-group-${DEFAULT_REPO_KEY}-recurring-total`)).toBeVisible();
    await expect(page.getByTestId(`repo-status-unblocker-${DEFAULT_REPO_KEY}`)).toHaveCount(0);
    release();
    await expect(page.getByTestId(`repo-status-unblocker-${DEFAULT_REPO_KEY}`)).toBeVisible();
  });

  test("degrades quietly, leaving the rest of the strip intact, when the unblocker request fails", async ({
    page,
  }) => {
    await page.route(UNBLOCKER_ACTIVITY_URL, async (route) => {
      await route.fulfill({ status: 500, json: { detail: "boom" } });
    });
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const strip = page.getByTestId(`repo-status-strip-${DEFAULT_REPO_KEY}`);
    await expect(strip).toBeVisible();
    await expect(page.getByTestId(`repo-status-group-${DEFAULT_REPO_KEY}-recurring-total`)).toBeVisible();
    await expect(page.getByTestId(`repo-status-unblocker-${DEFAULT_REPO_KEY}`)).toHaveCount(0);
    await expect(page.getByTestId(`repo-status-error-${DEFAULT_REPO_KEY}`)).toHaveCount(0);
    await expect(strip.locator("fieldset > div")).toHaveCount(3);
  });

  test("keeps every unblocker chip at the minimum tap height on a phone-width viewport and adds no page overflow", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    const pageOverflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    await stubUnblockerActivity(page, QUIET_ACTIVITY);
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(page.getByTestId(`repo-status-group-${DEFAULT_REPO_KEY}-recurring-total`)).toBeVisible();
    const overflowWithoutRow = await pageOverflow();

    await page.unroute(UNBLOCKER_ACTIVITY_URL);
    await stubUnblockerActivity(page, BUSY_ACTIVITY);
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const row = page.getByTestId(`repo-status-unblocker-${DEFAULT_REPO_KEY}`);
    await expect(row).toBeVisible();
    for (const link of await row.getByRole("link").all()) {
      const box = (await link.boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    const rowBox = (await row.boundingBox())!;
    expect(rowBox.x + rowBox.width).toBeLessThanOrEqual(375);
    expect(await pageOverflow()).toBeLessThanOrEqual(overflowWithoutRow);
  });
});
