import fs from "node:fs";
import path from "node:path";

import { waitForDispatcherIdle, waitForGraphNode } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

const IMPLEMENTATION_FLOW_FIXTURE_OWNER = "e2e-implementation-flow-co";
const IMPLEMENTATION_FLOW_FIXTURE_REPO = "widget";

test.beforeEach(async ({ request, e2eRoot }) => {
  const res = await request.post("/api/engines", {
    data: {
      repo: { owner: IMPLEMENTATION_FLOW_FIXTURE_OWNER, repo: IMPLEMENTATION_FLOW_FIXTURE_REPO },
    },
    headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(
      `implementation-flow.spec: failed to seed non-default engine (${String(res.status())})`,
    );
  }
});

test("implementation team runs inside a worktree, captures commits, then closes", async ({
  page,
  request,
}) => {
  test.setTimeout(150_000);
  const create = await request.post("/api/workspaces", {
    data: {
      title: `e2e-impl-${Date.now()}`,
      description: "playwright drove the implementation team",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(
    `/api/workspaces/${taskId}/dispatch`,
    { data: { team: "implementation" } },
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
      { timeout: 60_000, intervals: [200, 500, 1000] },
    )
    .toBe("success");

  await waitForDispatcherIdle(request, { timeout: 60_000 });

  await page.goto("/");
  await page.getByRole("link", { name: taskId }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: new RegExp(taskId) }),
  ).toBeVisible();

  const invNode = await waitForGraphNode(page, "invocation-node-inv_001");
  await invNode.click();
  await waitForGraphNode(page, "workflow-node-inv_001-implementer");
});
