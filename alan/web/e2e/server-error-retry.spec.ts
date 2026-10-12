import fs from "node:fs";
import path from "node:path";

import { waitForDispatcherIdle } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

test.beforeEach(async ({ request }) => {
  await waitForDispatcherIdle(request);
});

test.afterEach(({ e2eRoot }) => {
  const triggerFile = path.join(e2eRoot, "server-error-trigger");
  if (fs.existsSync(triggerFile)) {
    fs.rmSync(triggerFile);
  }
});

test("server-error auto-retry: job retried and workspace eventually terminates", async ({
  page,
  request,
  e2eRoot,
}) => {
  const triggerFile = path.join(e2eRoot, "server-error-trigger");
  const title = `e2e-server-error-${Date.now()}`;
  const createRes = await request.post("/api/workspaces", {
    data: {
      title,
      description: "server error retry test",
      budget: "medium",
    },
  });
  expect(createRes.status()).toBe(201);
  const { task_id } = (await createRes.json()) as { task_id: string };

  await page.goto(`/workspaces/${task_id}`);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();

  fs.writeFileSync(triggerFile, title);

  await request.post(`/api/workspaces/${task_id}/dispatch`, {
    data: { team: "research" },
  });

  await expect(page.getByTestId("status-active")).toBeVisible({ timeout: 5_000 });

  await expect
    .poll(
      async () => {
        const res = await request.get(`/api/workspaces/${task_id}`);
        const ws = (await res.json()) as { status: string };
        return ws.status;
      },
      { timeout: 30_000, intervals: [500] },
    )
    .toMatch(/^(completed|awaiting_input)$/);
});
