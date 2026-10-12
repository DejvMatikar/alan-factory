import type { Page } from "@playwright/test";

import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const DESKTOP_VIEWPORT = { width: 1280, height: 800 };
const MOBILE_VIEWPORT = { width: 390, height: 844 };
const DEFAULT_REPO_KEY = "DejvMatikar/alan";

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-home-ia-co");
});

async function waitForLayoutStable(page: Page): Promise<void> {
  const requiredStableSamples = 3;
  const maxWaitMs = 10_000;
  await page.waitForFunction(
    (required) => {
      const w = window as unknown as { __homeIaLayoutStable?: { prev: string; streak: number } };
      const header = document.querySelector("header");
      const tablist = document.querySelector('[role="tablist"]');
      const sample = `${String(header?.scrollWidth)}:${String(tablist?.scrollWidth)}`;
      const state = w.__homeIaLayoutStable ?? { prev: "", streak: 0 };
      const streak = sample === state.prev ? state.streak + 1 : 1;
      w.__homeIaLayoutStable = { prev: sample, streak };
      return streak >= required;
    },
    requiredStableSamples,
    { polling: 100, timeout: maxWaitMs },
  );
}

for (const [label, viewport] of Object.entries({ desktop: DESKTOP_VIEWPORT, "390px": MOBILE_VIEWPORT })) {
  test(
    `Repo page DOM order is the Active/Archived tablist inside the repository section, above the workspace content, at ${label}, with no overflow`,
    { tag: "@no-claude" },
    async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
      await expect(page.getByRole("tablist")).toBeVisible();
      await expect(page.getByTestId("health-indicator")).toHaveCount(0);
      await waitForLayoutStable(page);

      const tablistInsideSectionBeforeContent = await page.evaluate((repoKey: string) => {
        const tablist = document.querySelector('[role="tablist"]');
        const section = document.querySelector(`[data-testid="repo-section-${repoKey}"]`);
        const content = section?.querySelector(`table, [data-testid^="repo-empty-"]`) ?? null;
        return Boolean(
          tablist &&
            section &&
            content &&
            section.contains(tablist) &&
            tablist.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING,
        );
      }, DEFAULT_REPO_KEY);
      expect(tablistInsideSectionBeforeContent).toBe(true);

      await expect
        .poll(() => page.getByRole("tablist").evaluate((el) => el.scrollWidth))
        .toBeLessThanOrEqual(viewport.width);

      await expect
        .poll(() => page.getByRole("banner").evaluate((el) => el.scrollWidth))
        .toBeLessThanOrEqual(viewport.width);
    },
  );
}

test(
  "the repo dashboard's needs-you slot renders, with no cross-repository resume-drafts panel beside it",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(page.getByTestId("home-needs-you-slot")).toBeVisible();
    await expect(page.getByTestId("home-resume-drafts-slot")).toHaveCount(0);
  },
);

test(
  "Ctrl+Shift+E opens the Explore modal from a repo page with no prior focus",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(page.getByRole("tablist")).toBeVisible();
    await page.keyboard.press("Control+Shift+E");
    await expect(page.getByTestId("consultant-modal")).toBeVisible();
  },
);

test(
  "Ctrl+Shift+X navigates to Build, scoped to the current repo",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(page.getByRole("tablist")).toBeVisible();
    await page.keyboard.press("Control+Shift+X");
    await expect(page).toHaveURL(/\/build\?engine=/);
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
  },
);

test(
  "Ctrl+Shift+E opens the palette's consultant chooser with no repository selected, after the retired /inbox route redirects to Health",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/inbox");
    await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible();
    await expect(page).toHaveURL(/\/health$/);
    await page.keyboard.press("Control+Shift+E");
    await expect(page.getByTestId("command-palette")).toHaveAccessibleName(
      "Command palette — choose a consultant",
    );
    await expect(page.getByTestId("consultant-modal")).toHaveCount(0);
  },
);

test(
  "Ctrl+Shift+H fires nothing now that health-check has moved into each repo's own action row",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(page.getByRole("tablist")).toBeVisible();
    await page.keyboard.press("Control+Shift+H");
    await expect(page).toHaveURL((u) => new URL(u).pathname === `/repos/${DEFAULT_REPO_KEY}`);
  },
);

test(
  "every repo's actions are already directly visible, with no popover to open — Build/Explore in the rail, Maintenance on the dashboard",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(page.getByTestId(`repo-new-menu-${DEFAULT_REPO_KEY}`)).toHaveCount(0);
    await expect(page.getByTestId(`rail-start-build-${DEFAULT_REPO_KEY}`)).toBeVisible();
    await expect(page.getByTestId(`rail-start-explore-${DEFAULT_REPO_KEY}`)).toBeVisible();
    await expect(page.getByTestId(`repo-action-maintenance-${DEFAULT_REPO_KEY}`)).toBeVisible();
    await expect(page.getByTestId(`repo-action-build-${DEFAULT_REPO_KEY}`)).toHaveCount(0);
    await expect(page.getByTestId(`repo-action-explore-${DEFAULT_REPO_KEY}`)).toHaveCount(0);
  },
);

test(
  "the home table's Active column shows an explicit, accessibly-labelled empty state for a freshly created workspace with no recorded active time",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const res = await request.post("/api/workspaces", {
      data: { title: `e2e-home-ia-active-${String(Date.now())}`, description: "home-ia active column check" },
    });
    expect(res.ok()).toBeTruthy();
    const { task_id: taskId } = (await res.json()) as { task_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();

    const activeCell = row.getByTestId(`active-time-${taskId}`);
    await expect(activeCell).toHaveText("—");
    await expect(activeCell).toHaveAccessibleName(/not recorded/i);
    await expect(activeCell).toHaveAccessibleName(/excludes time waiting/i);
  },
);
