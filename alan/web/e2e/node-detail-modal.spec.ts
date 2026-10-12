import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext, Page } from "@playwright/test";

import { waitForDispatcherIdle, waitForGraphNode } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

const NODE_DETAIL_FIXTURE_OWNER = "e2e-node-detail-modal-co";
const NODE_DETAIL_FIXTURE_REPO = "widget";

test.beforeEach(async ({ request, e2eRoot }) => {
  const res = await request.post("/api/engines", {
    data: { repo: { owner: NODE_DETAIL_FIXTURE_OWNER, repo: NODE_DETAIL_FIXTURE_REPO } },
    headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(
      `node-detail-modal.spec: failed to seed non-default engine (${String(res.status())})`,
    );
  }
});

async function createCompletedResearchWorkspace(request: APIRequestContext): Promise<string> {
  const create = await request.post("/api/workspaces", {
    data: {
      title: `e2e-modal-${Date.now()}`,
      description: "node detail modal test",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(
    `/api/workspaces/${taskId}/dispatch`,
    { data: { team: "research" } },
  );
  expect(dispatch.ok()).toBeTruthy();

  await expect
    .poll(
      async () => {
        const res = await request.get(
          `/api/workspaces/${taskId}/invocations/inv_001`,
        );
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
  await page.goto("/");
  await page.getByRole("link", { name: taskId }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: new RegExp(taskId) }),
  ).toBeVisible();
}

test("output modal opens on a completed node and renders the result text", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const taskId = await createCompletedResearchWorkspace(request);
  await openWorkspacePage(page, taskId);

  const invNode = await waitForGraphNode(page, "invocation-node-inv_001");
  await invNode.click();

  const nodeCard = await waitForGraphNode(
    page,
    "workflow-node-inv_001-alpha_analyst",
  );
  await nodeCard.getByTestId("icon-output").click();

  await expect(page.getByTestId("node-detail-dialog")).toBeVisible();
  await expect(page.getByTestId("output-content")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("node-detail-dialog")).toContainText("STUB WORKER OUTPUT");
});

test("user opens a run's full view from the workspace page, then a step's details from there", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const taskId = await createCompletedResearchWorkspace(request);
  await openWorkspacePage(page, taskId);

  await waitForGraphNode(page, "invocation-node-inv_001");
  await page.getByRole("link", { name: "Open full view of run inv_001" }).click();
  await expect(page).toHaveURL(new RegExp(`/workspaces/${taskId}/invocations/inv_001$`));

  const card = page.getByTestId("workflow-node-alpha_analyst");
  await card.getByRole("button", { name: /Show activity of alpha_analyst/ }).click();
  await expect(page.getByTestId("stream-panel")).toBeVisible();

  await card.getByTestId("icon-output").click();
  const dialog = page.getByTestId("node-detail-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAccessibleName(/alpha_analyst/);
  await expect(dialog.getByRole("tab", { name: "Output", selected: true })).toBeFocused();
  await expect(page.getByTestId("output-content")).toBeVisible({ timeout: 5_000 });

  await page.keyboard.press("ArrowRight");
  await expect(dialog.getByRole("tab", { name: "Prompt", selected: true })).toBeVisible();
  await expect(page.getByTestId("prompt-content")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("prompt-resolved-text")).toBeVisible();
  await expect(page.getByTestId("prompt-resolved-not-recorded")).toHaveCount(0);

  await page.keyboard.press("ArrowRight");
  await expect(dialog.getByRole("tab", { name: "Config", selected: true })).toBeVisible();
  await expect(page.getByTestId("config-launch")).toBeVisible({ timeout: 5_000 });
  for (const field of ["model", "effort", "turn_limit", "permission_mode", "tools_effective", "session", "process"]) {
    const row = page.getByTestId(`config-launch-${field}`);
    await expect(row).toBeVisible();
    await expect(row).not.toContainText("not recorded for this run");
  }
  await expect(page.getByTestId("config-launch-session")).toContainText("New session");

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(card.getByTestId("icon-output")).toBeFocused();

  await page.getByRole("link", { name: /Back to workspace/ }).click();
  await expect(page).toHaveURL(new RegExp(`/workspaces/${taskId}$`));
  await waitForGraphNode(page, "invocation-node-inv_001");
});

test("keyboard-only: the run node's toggle still expands and collapses on its own, and Tab then Enter opens the full view", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const taskId = await createCompletedResearchWorkspace(request);
  await openWorkspacePage(page, taskId);

  const toggle = await waitForGraphNode(page, "invocation-node-inv_001");
  await toggle.focus();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByTestId("workflow-node-inv_001-alpha_analyst")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page).toHaveURL(new RegExp(`/workspaces/${taskId}$`));

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Open full view of run inv_001" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/workspaces/${taskId}/invocations/inv_001$`));
});
