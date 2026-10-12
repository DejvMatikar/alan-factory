import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext, Page } from "@playwright/test";

import { waitForDispatcherIdle } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

const RUNS_LIST_FIXTURE_OWNER = "e2e-workspace-runs-list-co";
const RUNS_LIST_FIXTURE_REPO = "widget";

test.beforeEach(async ({ request, e2eRoot }) => {
  const res = await request.post("/api/engines", {
    data: { repo: { owner: RUNS_LIST_FIXTURE_OWNER, repo: RUNS_LIST_FIXTURE_REPO } },
    headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(
      `workspace-runs-list.spec: failed to seed non-default engine (${String(res.status())})`,
    );
  }
});

async function createCompletedResearchWorkspace(
  request: APIRequestContext,
  title: string,
): Promise<string> {
  const create = await request.post("/api/workspaces", {
    data: { title, description: "runs list e2e fixture", budget: "medium" },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: { team: "research" },
  });
  expect(dispatch.ok()).toBeTruthy();

  await expect
    .poll(
      async () => {
        const res = await request.get(`/api/workspaces/${taskId}/invocations/inv_001`);
        if (!res.ok()) return "pending";
        const doc = (await res.json()) as { status: string };
        return doc.status;
      },
      { timeout: 30_000, intervals: [200, 500, 1000] },
    )
    .toBe("success");

  await waitForDispatcherIdle(request, { timeout: 60_000 });
  return taskId;
}

async function openWorkspacePage(page: Page, taskId: string): Promise<void> {
  await page.goto(`/workspaces/${taskId}`);
  await expect(page.getByRole("heading", { level: 1, name: new RegExp(taskId) })).toBeVisible();
}

test(
  "the runs section defaults to a list, and the Graph tab reveals the existing graph",
  async ({ page, request }) => {
    test.setTimeout(90_000);
    const taskId = await createCompletedResearchWorkspace(request, `e2e-runs-list-${Date.now()}`);
    await openWorkspacePage(page, taskId);

    const row = page.getByTestId("run-row-inv_001");
    await expect(row).toBeVisible();
    await expect(page.getByTestId("invocation-graph")).toHaveCount(0);

    await page.getByRole("tab", { name: "Graph" }).click();
    await expect(page.getByTestId("invocation-graph")).toBeVisible();
    await expect(page.getByTestId("invocation-node-inv_001")).toBeVisible();
    await expect(row).toHaveCount(0);

    await page.getByRole("tab", { name: "List" }).click();
    await expect(row).toBeVisible();
    await expect(page.getByTestId("invocation-graph")).toHaveCount(0);
  },
);

test(
  "the List / Graph tabs switch with the keyboard alone",
  async ({ page, request }) => {
    test.setTimeout(90_000);
    const taskId = await createCompletedResearchWorkspace(
      request,
      `e2e-runs-list-kbd-${Date.now()}`,
    );
    await openWorkspacePage(page, taskId);

    const listTab = page.getByRole("tab", { name: "List" });
    await listTab.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("invocation-graph")).toBeVisible();

    await page.keyboard.press("ArrowLeft");
    await expect(page.getByTestId("run-row-inv_001")).toBeVisible();
  },
);

test(
  "expanding a run row shows its steps and a link to the full run view",
  async ({ page, request }) => {
    test.setTimeout(90_000);
    const taskId = await createCompletedResearchWorkspace(
      request,
      `e2e-runs-list-steps-${Date.now()}`,
    );
    await openWorkspacePage(page, taskId);

    const row = page.getByTestId("run-row-inv_001");
    await row.getByRole("link", { name: "Open full view of run inv_001" }).click();
    await expect(page).toHaveURL(new RegExp(`/workspaces/${taskId}/invocations/inv_001$`));
    await page.getByRole("link", { name: /Back to workspace/ }).click();

    await row.getByTestId("run-row-summary-inv_001").click();
    await expect(page.locator('[data-testid^="run-row-step-"]').first()).toBeVisible();
  },
);
