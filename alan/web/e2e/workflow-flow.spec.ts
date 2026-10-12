import fs from "node:fs";
import path from "node:path";

import { waitForGraphNode } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

const WORKFLOW_FLOW_FIXTURE_OWNER = "e2e-workflow-flow-co";
const WORKFLOW_FLOW_FIXTURE_REPO = "widget";

test.beforeEach(async ({ request, e2eRoot }) => {
  const res = await request.post("/api/engines", {
    data: { repo: { owner: WORKFLOW_FLOW_FIXTURE_OWNER, repo: WORKFLOW_FLOW_FIXTURE_REPO } },
    headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(
      `workflow-flow.spec: failed to seed non-default engine (${String(res.status())})`,
    );
  }
});

test("user can dispatch research and see the workflow DAG inline on the workspace page", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const create = await request.post("/api/workspaces", {
    data: {
      title: `e2e-wf-${Date.now()}`,
      description: "playwright drove this",
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

  await page.goto("/");
  await page.getByRole("link", { name: taskId }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: new RegExp(taskId) }),
  ).toBeVisible();

  const invNode = await waitForGraphNode(page, "invocation-node-inv_001");
  await invNode.click();
  await waitForGraphNode(page, "workflow-node-inv_001-orchestrator");
  await expect(page.getByTestId("workflow-node-inv_001-alpha_analyst")).toBeVisible();
  await expect(page.getByTestId("workflow-node-inv_001-beta_analyst")).toBeVisible();
  await expect(page.getByTestId("workflow-node-inv_001-synthesizer")).toBeVisible();

  await expect(page.getByTestId("workspace-total-cost")).toBeVisible();
  await expect(page.getByTestId("workspace-total-cost")).toHaveText(
    /^Total: \$\d+\.\d+$/,
  );

  await expect(page.locator('[data-testid$="-usage"]').first()).toBeVisible();
  await expect(page.locator('[data-testid$="-usage"]').first()).toHaveText(
    /^\d+↑ \d+↓ · \$\d+\.\d+$/,
  );

  await page
    .getByTestId("workflow-node-inv_001-alpha_analyst")
    .getByRole("button", { name: /Show activity of alpha_analyst/ })
    .click();
  await expect(page.getByTestId("stream-panel")).toBeVisible();
  await expect(page.getByTestId("stream-stats-bar")).toBeVisible();
  await page.getByTestId("stream-stats-toggle").click();
  await expect(page.getByTestId("stream-row-result").first()).toBeVisible();
});
