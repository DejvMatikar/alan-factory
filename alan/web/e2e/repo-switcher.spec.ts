import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { seedControlToken } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

test.beforeEach(async ({ page, e2eRoot }) => {
  await seedControlToken(page, e2eRoot);
});

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

function rectsOverlap(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

async function registerEngine(
  request: APIRequestContext,
  token: string,
  owner: string,
  repo: string,
): Promise<void> {
  const res = await request.post("/api/engines", {
    data: { repo: { owner, repo } },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

test(
  "connecting a repository adds a switcher entry; choosing it updates the URL and survives a refresh",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-repo-switcher-owner-${suffix}`;
    const repo = `switch-me-${suffix}`;
    const slug = `${owner}/${repo}`;

    await page.goto("/health");
    await expect(page.getByTestId(`top-bar-repo-${slug}`)).toHaveCount(0);

    await registerEngine(request, token, owner, repo);
    await expect(page.getByTestId(`top-bar-repo-${slug}`)).toBeVisible({ timeout: 10_000 });

    await page.getByTestId(`top-bar-repo-${slug}`).click();
    await expect(page).toHaveURL(new RegExp(`/repos/${owner}/${repo}$`));
    await expect(page.getByTestId(`top-bar-repo-${slug}`)).toHaveAttribute("aria-current", "true");

    await page.reload();
    await expect(page).toHaveURL(new RegExp(`/repos/${owner}/${repo}$`));
    await expect(page.getByTestId(`top-bar-repo-${slug}`)).toHaveAttribute("aria-current", "true");
  },
);

test(
  "back and forward navigate between two repositories, and the scope persists across a cross-repo page",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const ownerA = `e2e-repo-switcher-a-${suffix}`;
    const ownerB = `e2e-repo-switcher-b-${suffix}`;
    const repo = "widget";

    await registerEngine(request, token, ownerA, repo);
    await registerEngine(request, token, ownerB, repo);

    await page.goto(`/repos/${ownerA}/${repo}`);
    await expect(page.getByTestId(`top-bar-repo-${ownerA}/${repo}`)).toHaveAttribute(
      "aria-current",
      "true",
    );

    await page.getByTestId(`top-bar-repo-${ownerB}/${repo}`).click();
    await expect(page).toHaveURL(new RegExp(`/repos/${ownerB}/${repo}$`));

    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/repos/${ownerA}/${repo}$`));
    await expect(page.getByTestId(`top-bar-repo-${ownerA}/${repo}`)).toHaveAttribute(
      "aria-current",
      "true",
    );

    await page.goForward();
    await expect(page).toHaveURL(new RegExp(`/repos/${ownerB}/${repo}$`));

    await page.goto("/health");
    await expect(page.getByTestId(`top-bar-repo-${ownerB}/${repo}`)).toHaveAttribute(
      "aria-current",
      "true",
    );
  },
);

test(
  "a repo link pasted cold into a fresh context opens straight to that repository",
  { tag: "@no-claude" },
  async ({ request, e2eRoot, context }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-repo-switcher-cold-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, owner, repo);

    const freshPage = await context.newPage();
    await freshPage.goto(`/repos/${owner}/${repo}`);
    await expect(freshPage.getByTestId(`top-bar-repo-${owner}/${repo}`)).toHaveAttribute(
      "aria-current",
      "true",
    );
    await expect(freshPage.getByRole("heading", { level: 1, name: "Widget" })).toBeVisible({
      timeout: 20_000,
    });
    await freshPage.close();
  },
);

test(
  "hiding the open repository from its own page announces the outcome, moves focus to the hidden-repos trigger, and navigates away",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-repo-switcher-hide-${suffix}`;
    const repo = "widget";
    const slug = `${owner}/${repo}`;
    await registerEngine(request, token, owner, repo);

    await page.goto(`/repos/${owner}/${repo}`);
    await page.getByTestId(`repo-action-hide-${slug}`).click();
    const hideAnnouncement = page.getByTestId("hidden-repo-announcement");
    await expect(hideAnnouncement).toBeVisible();
    await expect(hideAnnouncement).not.toHaveClass(/sr-only/);

    await expect(page).toHaveURL((u) => new URL(u).pathname !== `/repos/${owner}/${repo}`);
    await expect(page.getByTestId("hidden-repo-dialog-trigger")).toBeFocused();
    await expect(hideAnnouncement).toContainText("stopped cleanly");
    await expect(page.getByTestId(`top-bar-repo-${slug}`)).toHaveCount(0);
  },
);

test(
  "the hidden-repositories dialog opens with a count, restores a repo without a reload, and Escape returns focus to the trigger",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-repo-switcher-restore-${suffix}`;
    const repo = "widget";
    const slug = `${owner}/${repo}`;
    await registerEngine(request, token, owner, repo);

    const beforeHide = await request.get("/api/engines");
    const baselineCount = (
      (await beforeHide.json()) as { engines: { hidden?: boolean }[] }
    ).engines.filter((e) => e.hidden === true).length;

    await page.goto(`/repos/${owner}/${repo}`);
    const trigger = page.getByTestId("hidden-repo-dialog-trigger");
    await page.getByTestId(`repo-action-hide-${slug}`).click();
    const hideAnnouncement = page.getByTestId("hidden-repo-announcement");
    await expect(hideAnnouncement).toBeVisible();
    await expect(hideAnnouncement).not.toHaveClass(/sr-only/);

    await expect(trigger).toContainText(`Hidden (${String(baselineCount + 1)})`);
    await trigger.click();
    const dialog = page.getByTestId("hidden-repo-dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId(`hidden-repo-row-${slug}`)).toBeVisible();

    await page.getByTestId(`hidden-repo-restore-${slug}`).click();
    await expect(page.getByTestId("hidden-repo-announcement")).toContainText(`${slug} restored.`);
    await expect(page.getByTestId(`hidden-repo-row-${slug}`)).toHaveCount(0);
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();

    await expect(page.getByTestId(`top-bar-repo-${slug}`)).toBeVisible();
  },
);

test(
  "with nothing connected, the top bar shows no repository entries and the blank slate offers one connect action",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.route("**/api/engines", async (route) => {
      await route.fulfill({ json: { engines: [] } });
    });
    await page.goto("/");
    await expect(page.getByTestId("home-blank-slate")).toBeVisible();
    await expect(page.getByTestId("top-bar-repo-switcher")).toHaveCount(0);
    await expect(page.getByTestId("top-bar-repo-select")).toHaveCount(0);
    await expect(page.getByTestId("hidden-repo-dialog-trigger")).toContainText(
      "Hidden (0)",
    );
    const filled = page.locator(
      '[data-variant="create"]:visible:not([data-icon-only]), [data-variant="action"]:visible:not([data-icon-only]), [data-variant="destroy"]:visible:not([data-icon-only]), [data-variant="reset"]:visible:not([data-icon-only])',
    );
    await expect(filled).toHaveCount(1);
    await expect(filled.first()).toHaveAttribute("data-testid", "connect-repo-cta");
  },
);

test(
  "every switcher entry is keyboard reachable with a visible focus ring, and Enter activates it",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-repo-switcher-kbd-${suffix}`;
    const repo = "widget";
    const slug = `${owner}/${repo}`;
    await registerEngine(request, token, owner, repo);

    await page.goto(`/repos/${owner}/${repo}`);
    const entry = page.getByTestId(`top-bar-repo-${slug}`);
    await entry.focus();
    await expect(entry).toBeFocused();
    await expect(entry).toHaveClass(/focus-visible:ring-2/);
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/repos/${owner}/${repo}$`));
  },
);

test(
  "at a narrow viewport the repository switcher list is hidden, and the repo drawer trigger opens it as a list inside the drawer instead",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-repo-switcher-narrow-${suffix}`;
    const repo = "widget";
    const slug = `${owner}/${repo}`;
    await registerEngine(request, token, owner, repo);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/repos/${owner}/${repo}`);
    await expect(page.getByTestId("top-bar-repo-switcher")).not.toBeVisible();

    await page.getByTestId("repo-drawer-trigger").click();
    await expect(page.getByTestId("repo-drawer")).toBeVisible();
    await expect(page.getByTestId(`repo-drawer-repo-${slug}`)).toHaveAttribute(
      "aria-current",
      "true",
    );
  },
);

test(
  "at a narrow viewport outside the repo shell, the connect action and drawer trigger never overlap the projects/hidden/health/settings cluster",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-repo-switcher-overlap-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, owner, repo);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/health");

    const leftClusterTestIds = ["top-bar-repo-drawer-trigger", "connect-repo-cta"];
    const rightClusterTestIds = [
      "project-features-toggle",
      "hidden-repo-dialog-trigger",
      "nav-health-badge",
      "settings-gear",
    ];

    const leftBoxes = await Promise.all(
      leftClusterTestIds.map((testId) => page.getByTestId(testId).boundingBox()),
    );
    const rightBoxes = await Promise.all(
      rightClusterTestIds.map((testId) => page.getByTestId(testId).boundingBox()),
    );

    for (const leftBox of leftBoxes) {
      expect(leftBox).not.toBeNull();
    }
    for (const rightBox of rightBoxes) {
      expect(rightBox).not.toBeNull();
    }

    for (const leftBox of leftBoxes) {
      for (const rightBox of rightBoxes) {
        expect(rectsOverlap(leftBox as Rect, rightBox as Rect)).toBe(false);
      }
    }
  },
);

test(
  "at a narrow viewport the projects toggle's revealed description stays inside the viewport",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/health");

    await page.getByTestId("project-features-toggle").focus();
    const description = page.locator("#project-features-toggle-description");
    await expect(description).toBeVisible();

    const box = await description.boundingBox();
    expect(box).not.toBeNull();
    const { x, width } = box as Rect;
    expect(x).toBeGreaterThanOrEqual(0);
    expect(x + width).toBeLessThanOrEqual(390);
  },
);
