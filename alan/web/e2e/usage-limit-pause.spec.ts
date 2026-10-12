import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { waitForDispatcherIdle } from "./fixtures/helpers";
import { seedControlToken } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

function clearQueuePause(root: string): void {
  const dbPath = path.join(root, "queue.db");
  execFileSync("python3", [
    "-c",
    `import sqlite3; c=sqlite3.connect(${JSON.stringify(dbPath)}); c.execute("DELETE FROM queue_meta WHERE key='paused_until'"); c.commit(); c.close()`,
  ]);
}

test.beforeEach(async ({ request, e2eRoot }) => {
  clearQueuePause(e2eRoot);
  await waitForDispatcherIdle(request);
});

test.afterEach(({ e2eRoot }) => {
  const triggerFile = path.join(e2eRoot, "usage-limit-trigger");
  if (fs.existsSync(triggerFile)) {
    fs.rmSync(triggerFile);
  }
  clearQueuePause(e2eRoot);
});

test("queue-paused banner appears on usage limit and disappears when pause expires", async ({
  page,
  request,
  e2eRoot,
}) => {
  const triggerFile = path.join(e2eRoot, "usage-limit-trigger");
  const title = `e2e-usage-limit-${Date.now()}`;
  const createRes = await request.post("/api/workspaces", {
    data: { title, description: "pause banner test", budget: "medium" },
  });
  expect(createRes.status()).toBe(201);
  const { task_id } = (await createRes.json()) as { task_id: string };

  await page.goto(`/workspaces/${task_id}`);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();

  fs.writeFileSync(triggerFile, title);

  await request.post(`/api/workspaces/${task_id}/dispatch`, {
    data: { team: "research" },
  });

  const banner = page.getByTestId("queue-paused-banner");
  await expect(banner).toBeVisible({ timeout: 15_000 });
  await expect(banner).toHaveText(/resumes at \d{1,2}:\d{2}/);

  const healthRes = await request.get("/api/health");
  const health = (await healthRes.json()) as {
    queue_paused: boolean;
    paused_until: number | null;
  };
  expect(health.queue_paused).toBe(true);
  expect(health.paused_until).not.toBeNull();

  const wsRes = await request.get(`/api/workspaces/${task_id}`);
  const ws = (await wsRes.json()) as { status: string };
  expect(ws.status).toBe("active");

  clearQueuePause(e2eRoot);

  await expect(banner).not.toBeVisible({ timeout: 10_000 });
});

test("queue-resume-button clears the pause and announces it accessibly", async ({
  page,
  request,
  e2eRoot,
}) => {
  await seedControlToken(page, e2eRoot);
  const triggerFile = path.join(e2eRoot, "usage-limit-trigger");
  const title = `e2e-usage-limit-resume-${Date.now()}`;
  const createRes = await request.post("/api/workspaces", {
    data: { title, description: "resume button test", budget: "medium" },
  });
  expect(createRes.status()).toBe(201);
  const { task_id } = (await createRes.json()) as { task_id: string };

  await page.goto(`/workspaces/${task_id}`);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();

  fs.writeFileSync(triggerFile, title);

  await request.post(`/api/workspaces/${task_id}/dispatch`, {
    data: { team: "research" },
  });

  const banner = page.getByTestId("queue-paused-banner");
  await expect(banner).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("queue-resume-button").click();

  await expect(banner).not.toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("queue-resume-announcement")).toHaveText("Queue resumed.");
});

test(
  "resuming the queue without a control token is refused with 401",
  { tag: "@no-claude" },
  async ({ request }) => {
    const res = await request.post("/api/queue/resume");
    expect(res.status()).toBe(401);
  },
);
