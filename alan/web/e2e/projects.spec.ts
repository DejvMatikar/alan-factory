import type { APIRequestContext } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";
import { seedProjectFeatures } from "./fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

const PROJECTS_SPEC_FIXTURE_OWNER = "e2e-projects-spec-co";
const PROJECTS_SPEC_FIXTURE_REPO = "widget";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

async function ensureNonDefaultEngine(request: APIRequestContext, root: string): Promise<void> {
  const res = await request.post("/api/engines", {
    data: { repo: { owner: PROJECTS_SPEC_FIXTURE_OWNER, repo: PROJECTS_SPEC_FIXTURE_REPO } },
    headers: { "X-Alan-Control-Token": readControlToken(root) },
  });
  if (res.ok() || res.status() === 409) return;
  throw new Error(`projects.spec: failed to seed non-default engine (${String(res.status())})`);
}

test(
  "test_e2e_project_create_roundtrip: create a project in the UI, dispatch a workspace under it via API, see it in the detail ledger",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Project ${Date.now()}`;

    await page.goto("/projects");
    await page.getByTestId("new-project-button").click();
    await expect(page.getByTestId("new-project-dialog")).toBeVisible();
    await page.getByTestId("new-project-title").fill(title);
    await page.getByTestId("new-project-submit").click();

    await expect(page.getByTestId("new-project-dialog")).toBeHidden({ timeout: 10_000 });
    await page.waitForURL(/\/projects\/[^/]+$/);
    const projectId = new URL(page.url()).pathname.split("/projects/")[1];
    expect(projectId).toBeTruthy();

    const wsTitle = `E2E Project Workspace ${Date.now()}`;
    const create = await request.post("/api/workspaces", {
      data: {
        title: wsTitle,
        description: "workspace dispatched under a project",
        project_id: projectId,
      },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    await page.reload();
    const wsLink = page.getByRole("link", { name: wsTitle });
    await expect(wsLink).toBeVisible({ timeout: 10_000 });
    await expect(wsLink).toHaveAttribute("href", `/workspaces/${taskId}`);
  },
);

test(
  "Projects list row shows workspace count and total spend consistent with the detail page",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Project Stats ${Date.now()}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const project = (await create.json()) as { id: string };

    const wsTitle = `E2E Project Stats WS ${Date.now()}`;
    const wsCreate = await request.post("/api/workspaces", {
      data: { title: wsTitle, description: "", project_id: project.id },
    });
    expect(wsCreate.ok()).toBeTruthy();

    await page.goto("/projects");
    const stats = page.getByTestId(`project-stats-${project.id}`);
    await expect(stats).toBeVisible({ timeout: 10_000 });
    await expect(stats).toContainText("1 workspace");

    const detail = await request.get(`/api/projects/${project.id}`);
    expect(detail.ok()).toBeTruthy();
    const detailBody = (await detail.json()) as { workspaces: unknown[] };
    expect(detailBody.workspaces).toHaveLength(1);
  },
);

test(
  "the New project affordance is reachable from the default repo section's footer link",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    await ensureNonDefaultEngine(request, e2eRoot);
    await page.goto("/");
    await page.getByTestId("repo-footer-DejvMatikar/alan").getByRole("link").click();
    await expect(page).toHaveURL(/\/projects$/);
    await expect(page.getByTestId("new-project-button")).toBeVisible();
  },
);

test(
  "Projects page has a single top-level heading and the create-error renders in the destructive colour",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();

    await page.route("**/api/projects", async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      await route.fulfill({ status: 500, json: { detail: "create-boom" } });
    });
    await page.getByTestId("new-project-button").click();
    await page.getByTestId("new-project-title").fill("Boom");
    await page.getByTestId("new-project-submit").click();

    const errorEl = page.getByTestId("new-project-error");
    await expect(errorEl).toBeVisible();
    await expect(errorEl).toHaveClass(/text-btn-destroy/);
  },
);

test(
  "the Projects list hides an archived project from the Active tab and reveals it under Archived",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Tab Project ${Date.now()}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const project = (await create.json()) as { id: string };

    const archive = await request.patch(`/api/projects/${project.id}`, {
      data: { status: "archived" },
    });
    expect(archive.ok()).toBeTruthy();

    await page.goto("/projects");
    await expect(page.getByRole("tab", { name: "Active" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByTestId("projects-loading")).toBeHidden();
    await expect(page.getByRole("link", { name: title })).toHaveCount(0);

    await page.getByRole("tab", { name: "Archived" }).click();
    await expect(page.getByRole("link", { name: title })).toBeVisible();
    await expect(page.getByTestId(`project-status-archived`)).toBeVisible();

    await page.reload();
    await expect(page.getByRole("tab", { name: "Archived" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByRole("link", { name: title })).toBeVisible();
  },
);

test(
  "switching a project's autonomy dial to auto-continue persists and shows scoped help copy",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Autonomy Project ${Date.now()}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const project = (await create.json()) as { id: string };

    await page.goto(`/projects/${project.id}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();

    const help = page.getByTestId("project-autonomy-help");
    await expect(help).toContainText("never resolves a decision that needs you");
    await expect(help.getByRole("link")).toHaveCount(0);

    await page.getByTestId("project-autonomy-select").selectOption("auto_continue");
    await page.getByTestId("project-autonomy-save").click();
    await expect(page.getByTestId("project-autonomy-saved")).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();
    await expect(page.getByTestId("project-autonomy-select")).toHaveValue("auto_continue");
  },
);
