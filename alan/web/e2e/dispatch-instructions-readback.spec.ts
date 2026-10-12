import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { waitForGraphNode } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

const FIXTURE_OWNER = "e2e-dispatch-instructions-readback-co";
const FIXTURE_REPO = "widget";

test.beforeEach(async ({ request, e2eRoot }) => {
  const res = await request.post("/api/engines", {
    data: { repo: { owner: FIXTURE_OWNER, repo: FIXTURE_REPO } },
    headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(
      `dispatch-instructions-readback.spec: failed to seed non-default engine (${String(res.status())})`,
    );
  }
});

async function createAndDispatch(
  request: APIRequestContext,
  title: string,
  instructions: string | undefined,
): Promise<string> {
  const create = await request.post("/api/workspaces", {
    data: { title, description: "dispatch-instructions readback e2e", budget: "medium" },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: instructions === undefined ? { team: "research" } : { team: "research", instructions },
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

  return taskId;
}

test("shows an 'instructions attached' badge in the graph listing, and reveals the full text on the invocation page", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const taskId = await createAndDispatch(
    request,
    `e2e-instr-attached-${Date.now()}`,
    "focus on the widget module",
  );

  await page.goto("/");
  await page.getByRole("link", { name: taskId }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: new RegExp(taskId) }),
  ).toBeVisible();

  const invNode = await waitForGraphNode(page, "invocation-node-inv_001");
  await expect(invNode.getByTestId("instruction-badge-inv_001")).toHaveText(
    "instructions attached",
  );
  await expect(invNode.getByTestId("instruction-badge-inv_001-details")).toHaveCount(0);

  await page.goto(`/workspaces/${taskId}/invocations/inv_001`);
  const badge = page.getByTestId("instruction-badge-inv_001");
  await expect(badge).toBeVisible();
  await expect(badge).toHaveText("instructions attached");

  const textPane = page.getByTestId("instruction-badge-inv_001-text");
  await expect(textPane).toBeHidden();
  await badge.click();
  await expect(textPane).toBeVisible();
  await expect(textPane).toContainText("focus on the widget module");
});

test("shows a muted 'no instructions' badge for a run dispatched without instructions", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const taskId = await createAndDispatch(
    request,
    `e2e-instr-none-${Date.now()}`,
    undefined,
  );

  await page.goto(`/workspaces/${taskId}/invocations/inv_001`);
  const badge = page.getByTestId("instruction-badge-inv_001");
  await expect(badge).toBeVisible();
  await expect(badge).toHaveText("no instructions");
  await expect(page.getByTestId("instruction-badge-inv_001-details")).toHaveCount(0);
});
