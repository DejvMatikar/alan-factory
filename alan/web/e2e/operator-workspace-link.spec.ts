import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext, Page } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";

async function createWorkspace(
  request: APIRequestContext,
  e2eRoot: string,
  title: string,
  status: string | null,
): Promise<string> {
  const create = await request.post("/api/workspaces", {
    data: { title, description: "operator link e2e" },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };
  if (status !== null) {
    const metaPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "meta.json");
    const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
    meta.status = status;
    fs.writeFileSync(metaPath, JSON.stringify(meta));
  }
  return taskId;
}

async function stubNoSession(page: Page): Promise<void> {
  await page.route("**/api/operator/status", async (route) => {
    await route.fulfill({ json: { session: null, unavailable: null } });
  });
}

test(
  "a stuck workspace's header links to the terminal already scoped to that workspace",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const taskId = await createWorkspace(request, e2eRoot, "e2e-operator-link-stuck", "escalated");
    await stubNoSession(page);

    await page.goto(`/workspaces/${taskId}`);
    const link = page.getByTestId("workspace-operator-link");
    await expect(link).toBeVisible();
    await link.click();

    await expect(page).toHaveURL(
      (url) => url.pathname === "/operator" && url.search === `?scope=workspace&target=${taskId}`,
    );
    await expect(page.getByTestId("operator-scope-dialog")).toBeVisible();
    await expect(page.getByTestId("operator-scope-workspace")).toBeChecked();
    const target = page.getByTestId("operator-target");
    await expect(target).toHaveValue(taskId);
    await expect(target.locator("option:checked")).toContainText("e2e-operator-link-stuck");
    await expect(target.locator("option:checked")).toContainText("escalated");
    await expect(page.getByTestId("operator-start-submit")).toBeEnabled();
  },
);

test(
  "a link to a workspace that is no longer stuck still preselects it in the dropdown",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const taskId = await createWorkspace(request, e2eRoot, "e2e-operator-link-recovered", null);
    await stubNoSession(page);

    await page.goto(`/operator?scope=workspace&target=${taskId}`);

    const target = page.getByTestId("operator-target");
    await expect(target).toHaveValue(taskId);
    await expect(target.locator("option:checked")).toContainText("e2e-operator-link-recovered");
    await expect(page.getByTestId("operator-start-submit")).toBeEnabled();
  },
);

test(
  "a workspace that is not stuck has no operator link in its header",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const taskId = await createWorkspace(request, e2eRoot, "e2e-operator-link-healthy", null);

    await page.goto(`/workspaces/${taskId}`);

    await expect(page.getByTestId("workspace-back-link")).toBeVisible();
    await expect(page.getByTestId("workspace-operator-link")).toHaveCount(0);
  },
);
