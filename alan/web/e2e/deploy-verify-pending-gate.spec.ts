import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";

function pyLiteral(value: string | null): string {
  return value === null ? "None" : JSON.stringify(value);
}

function insertDeployVerifyJob(
  root: string,
  taskId: string,
  instructions: string | null,
  eligibleAt: number,
): void {
  const dbPath = path.join(root, "queue.db");
  execFileSync("python3", [
    "-c",
    `import sqlite3
conn = sqlite3.connect(${JSON.stringify(dbPath)})
conn.execute(
    "INSERT INTO jobs (task_id, workspace_id, team, status, enqueued_at, instructions, eligible_at) "
    "VALUES (?, ?, 'deploy_verify', 'pending', datetime('now'), ?, ?)",
    (${JSON.stringify(taskId)}, ${JSON.stringify(taskId)}, ${pyLiteral(instructions)}, ${eligibleAt}),
)
conn.commit()
conn.close()
`,
  ]);
}

function gateRecordJson(opts: {
  path: "awaiting_restart" | "awaiting_live";
  deferredAt: number;
  deadline: number | null;
  raceDeadline: number | null;
  mergedAt: number | null;
}): string {
  return JSON.stringify({
    kind: "deploy_gate_v1",
    merge_sha: "abc123def456",
    path: opts.path,
    deferred_at: opts.deferredAt,
    deadline: opts.deadline,
    race_deadline: opts.raceDeadline,
    merged_at: opts.mergedAt,
    repo: "DejvMatikar/alan",
  });
}

test(
  "pending deploy-gate notice renders for a deliberately-parked verification attempt",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const create = await request.post("/api/workspaces", {
      data: { title: "e2e-deploy-gate-parked", description: "test pending deploy gate notice" },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    const now = Date.now() / 1000;
    insertDeployVerifyJob(
      e2eRoot,
      taskId,
      gateRecordJson({
        path: "awaiting_live",
        deferredAt: now - 90,
        deadline: now + 5_400,
        raceDeadline: null,
        mergedAt: now - 150,
      }),
      now + 3_600,
    );

    await page.goto(`/workspaces/${taskId}`);

    const notice = page.getByTestId("pending-deploy-gate-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toHaveAttribute("aria-label", "Deploy verification status");
    await expect(notice).toContainText("waiting on purpose");
    await expect(notice).toContainText("confirming the merge is live via local observation");
  },
);

test(
  "pending deploy-gate notice is absent for a queued job that has not evaluated yet",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const create = await request.post("/api/workspaces", {
      data: { title: "e2e-deploy-gate-queued", description: "test pending deploy gate absence" },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    const now = Date.now() / 1000;
    insertDeployVerifyJob(e2eRoot, taskId, null, now + 3_600);

    await page.goto(`/workspaces/${taskId}`);

    await expect(
      page.getByRole("heading", { level: 1, name: "e2e-deploy-gate-queued" }),
    ).toBeVisible();
    await expect(page.getByTestId("pending-deploy-gate-notice")).toHaveCount(0);
  },
);

function setWorkspaceMeta(
  e2eRoot: string,
  taskId: string,
  fields: Record<string, unknown>,
): void {
  const metaPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "meta.json");
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
  fs.writeFileSync(metaPath, JSON.stringify({ ...meta, ...fields }, null, 2));
}

test(
  "a completed workspace renders a 'Live since' label once deploy verification confirms live_at",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const create = await request.post("/api/workspaces", {
      data: { title: "e2e-deploy-gate-live-since", description: "test live-since label" },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    setWorkspaceMeta(e2eRoot, taskId, {
      status: "completed",
      live_at: "2026-05-21T09:30:00Z",
    });

    await page.goto(`/workspaces/${taskId}`);

    await expect(page.getByTestId("delivery-summary-panel")).toBeVisible();
    const liveSince = page.getByTestId("live-since");
    await expect(liveSince).toBeVisible();
    await expect(liveSince).toContainText("Live since");
  },
);

test(
  "a completed workspace omits the 'Live since' label when live_at is not yet set",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const create = await request.post("/api/workspaces", {
      data: { title: "e2e-deploy-gate-no-live-since", description: "test absent live-since label" },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    setWorkspaceMeta(e2eRoot, taskId, { status: "completed" });

    await page.goto(`/workspaces/${taskId}`);

    await expect(page.getByTestId("situation-card")).toBeVisible();
    await expect(page.getByTestId("live-since")).toHaveCount(0);
  },
);
