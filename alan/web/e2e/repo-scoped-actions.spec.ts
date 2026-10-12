import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";
import { seedProjectFeatures } from "./fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

const DEFAULT_REPO_KEY = "DejvMatikar/alan";

const FILLED_VARIANT_SELECTOR =
  '[data-variant="create"]:visible:not([data-icon-only]), [data-variant="action"]:visible:not([data-icon-only]), [data-variant="destroy"]:visible:not([data-icon-only]), [data-variant="reset"]:visible:not([data-icon-only])';

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

const REPO_SCOPED_FIXTURE_OWNER = "e2e-repo-scoped-actions-co";
const REPO_SCOPED_FIXTURE_REPO = "widget";

test.beforeEach(async ({ request, e2eRoot }) => {
  const res = await request.post("/api/engines", {
    data: { repo: { owner: REPO_SCOPED_FIXTURE_OWNER, repo: REPO_SCOPED_FIXTURE_REPO } },
    headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(
      `repo-scoped-actions.spec: failed to seed non-default engine (${String(res.status())})`,
    );
  }
});

test(
  "test_e2e_create_project_from_repo_scope: starting a project from a repository's own toolbar creates it, scoped to that repository",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/");
    const title = `E2E Repo Scope Project ${Date.now()}`;

    await page.getByTestId(`repo-start-project-${DEFAULT_REPO_KEY}`).click();
    await expect(page.getByTestId("new-project-dialog")).toBeVisible();
    await page.getByTestId("new-project-title").fill(title);
    await page.getByTestId("new-project-submit").click();
    await expect(page.getByTestId("new-project-dialog")).toBeHidden({ timeout: 10_000 });

    const notice = page.getByTestId(`repo-bind-notice-${DEFAULT_REPO_KEY}`);
    await expect(notice).toBeVisible({ timeout: 10_000 });
    await expect(notice).toContainText(title);
    await expect(notice).toContainText("not yet bound to this repository");

    const link = notice.getByRole("link", { name: title });
    const href = await link.getAttribute("href");
    expect(href).toMatch(/^\/projects\//);
  },
);

test(
  "an observe-posture repository still shows Maintenance directly, as a ghost link to its posture setting",
  { tag: "@no-claude" },
  async ({ page, e2eRoot, request }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-observe-owner-${suffix}`;
    const repo = `e2e-observe-repo-${suffix}`;
    const slug = `${owner}/${repo}`;

    const createRes = await request.post("/api/engines", {
      data: { repo: { owner, repo } },
      headers: { "X-Alan-Control-Token": token },
    });
    expect(createRes.status()).toBe(201);

    await page.goto(`/repos/${owner}/${repo}`);
    await expect(page.getByTestId(`rail-start-build-${slug}`)).toBeVisible();
    await expect(page.getByTestId(`rail-start-explore-${slug}`)).toBeVisible();
    const maintenance = page.getByTestId(`repo-action-maintenance-${slug}`);
    await expect(maintenance).toBeVisible();
    await expect(maintenance).toHaveAttribute("href", `/connect/${owner}/${repo}/posture`);
  },
);

test(
  "clicking Maintenance dispatches it scoped to a specific non-default repository and reports it without leaving that repository's own page",
  { tag: "@no-claude" },
  async ({ page, e2eRoot, request }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-repo-scope-owner-${suffix}`;
    const repo = `e2e-repo-scope-repo-${suffix}`;
    const slug = `${owner}/${repo}`;
    const headers = { "X-Alan-Control-Token": token };

    const createRes = await request.post("/api/engines", {
      data: { repo: { owner, repo } },
      headers,
    });
    expect(createRes.status()).toBe(201);
    const postureRes = await request.post(`/api/engines/${owner}/${repo}/posture`, {
      data: { posture: "autonomous", confirm: true },
      headers,
    });
    expect(postureRes.status()).toBe(200);

    const dispatch: { body: { team: string; engine_key: string } | null } = { body: null };
    await page.route("**/api/workspaces", async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      dispatch.body = route.request().postDataJSON() as { team: string; engine_key: string };
      await route.fulfill({
        status: 201,
        json: { task_id: "e2e-repo-scope-ws", root_task_ids: ["e2e-repo-scope-ws"] },
      });
    });
    await page.route("**/api/workspaces/e2e-repo-scope-ws/stop", async (route) => {
      await route.fulfill({ status: 204 });
    });

    await page.goto(`/repos/${owner}/${repo}`);
    await page.getByTestId(`repo-action-maintenance-${slug}`).click();
    const notice = page.getByTestId(`repo-maintenance-notice-${slug}`);
    await expect(notice).toBeVisible();
    await expect(notice.getByRole("link")).toHaveAttribute("href", "/workspaces/e2e-repo-scope-ws");
    await expect(page).toHaveURL((u) => new URL(u).pathname === `/repos/${owner}/${repo}`);

    expect(dispatch.body).not.toBeNull();
    expect(dispatch.body?.team).toBe("maintenance");
    expect(dispatch.body?.engine_key).toBe(slug);

    await page.getByTestId(`repo-maintenance-stop-${slug}`).click();
    await expect(notice).toContainText("Stopped");
    await expect(page.getByTestId(`repo-maintenance-stop-${slug}`)).toHaveCount(0);
  },
);

test(
  "the rail's Build control pre-binds the repository and skips the chooser, even with multiple repos connected",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await page.getByTestId(`rail-start-build-${DEFAULT_REPO_KEY}`).click();
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
    await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 15_000 });
    await expect(page.getByTestId("repo-chooser-modal")).toHaveCount(0);
    expect(new URL(page.url()).searchParams.get("engine")).toBe(DEFAULT_REPO_KEY);
  },
);

test(
  "a non-default repository's rail Build control pre-binds that repository and skips the chooser",
  { tag: "@no-claude" },
  async ({ page }) => {
    const slug = `${REPO_SCOPED_FIXTURE_OWNER}/${REPO_SCOPED_FIXTURE_REPO}`;
    await page.goto(`/repos/${slug}`);
    await page.getByTestId(`rail-start-build-${slug}`).click();
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
    await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 15_000 });
    await expect(page.getByTestId("repo-chooser-modal")).toHaveCount(0);
    expect(new URL(page.url()).searchParams.get("engine")).toBe(slug);
  },
);

test(
  "the card's health control links into the health screen filtered to that repository",
  { tag: "@no-claude" },
  async ({ page }) => {
    const slug = `${REPO_SCOPED_FIXTURE_OWNER}/${REPO_SCOPED_FIXTURE_REPO}`;
    await page.goto(`/repos/${slug}`);
    await page.getByTestId(`repo-health-${slug}`).click();
    await expect(page).toHaveURL(`/health?engine=${encodeURIComponent(slug)}`);
    await expect(page.getByTestId("health-scope-indicator")).toContainText(slug);
  },
);

for (const [label, viewport, expectedTestIds] of [
  [
    "desktop",
    { width: 1280, height: 800 },
    ["connect-repo-cta", `rail-start-build-${DEFAULT_REPO_KEY}`, `rail-start-explore-${DEFAULT_REPO_KEY}`],
  ],
  ["375px", { width: 375, height: 812 }, ["connect-repo-cta"]],
] as const) {
  test(
    `only the connect-repo CTA and the default repo's visible Build/Explore controls render as saturated-fill on Home at ${label}`,
    { tag: "@no-claude" },
    async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto("/");
      const connectCta = page.getByTestId("connect-repo-cta");
      await expect(connectCta).toBeVisible();
      const filled = page.locator(FILLED_VARIANT_SELECTOR);
      await expect(filled).toHaveCount(expectedTestIds.length);
      const testIds = await filled.evaluateAll((els) => els.map((el) => el.getAttribute("data-testid")));
      expect(new Set(testIds)).toEqual(new Set(expectedTestIds));
    },
  );
}
