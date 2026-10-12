import type { APIRequestContext, Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { seedBlockingQuestion } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

async function createWorkspace(request: APIRequestContext, title: string): Promise<string> {
  const create = await request.post("/api/workspaces", {
    data: { title, description: "test situation card geometry" },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };
  return taskId;
}

function setStatus(e2eRoot: string, taskId: string, status: string): void {
  const metaPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "meta.json");
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
  meta.status = status;
  fs.writeFileSync(metaPath, JSON.stringify(meta));
}

function deployGateRecordJson(now: number): string {
  return JSON.stringify({
    kind: "deploy_gate_v1",
    merge_sha: "abc123def456",
    path: "awaiting_live",
    deferred_at: now - 90,
    deadline: now + 5_400,
    race_deadline: null,
    merged_at: now - 150,
    repo: "DejvMatikar/alan",
  });
}

function insertDeployVerifyJob(root: string, taskId: string, eligibleAt: number): void {
  const dbPath = path.join(root, "queue.db");
  const instructions = deployGateRecordJson(Date.now() / 1000);
  execFileSync("python3", [
    "-c",
    `import sqlite3
conn = sqlite3.connect(${JSON.stringify(dbPath)})
conn.execute(
    "INSERT INTO jobs (task_id, workspace_id, team, status, enqueued_at, instructions, eligible_at) "
    "VALUES (?, ?, 'deploy_verify', 'pending', datetime('now'), ?, ?)",
    (${JSON.stringify(taskId)}, ${JSON.stringify(taskId)}, ${JSON.stringify(instructions)}, ${eligibleAt}),
)
conn.commit()
conn.close()
`,
  ]);
}

async function assertSingleCardWithinViewport(page: Page): Promise<void> {
  const cards = page.getByTestId("situation-card");
  await expect(cards).toHaveCount(1);
  const box = await cards.boundingBox();
  expect(box).not.toBeNull();
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  expect(box!.width).toBeGreaterThan(0);
  expect(box!.height).toBeGreaterThan(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport!.width + 1);
}

const STATUS_HEADLINES: readonly { status: string; headline: string }[] = [
  { status: "paused", headline: "Paused" },
  { status: "completed", headline: "Completed" },
  { status: "escalated", headline: "Escalated" },
  { status: "failed", headline: "Failed" },
  { status: "partial", headline: "Partial" },
  { status: "archived", headline: "Archived" },
];

for (const { status, headline } of STATUS_HEADLINES) {
  test(
    `situation card renders one bounded card with the ${status} headline`,
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const taskId = await createWorkspace(request, `e2e-situation-${status}`);
      setStatus(e2eRoot, taskId, status);

      await page.goto(`/workspaces/${taskId}`);

      await expect(page.getByTestId("situation-headline")).toContainText(headline);
      await assertSingleCardWithinViewport(page);
    },
  );
}

test(
  "situation card renders the Running headline and body for a plain active workspace",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const taskId = await createWorkspace(request, "e2e-situation-running");

    await page.goto(`/workspaces/${taskId}`);

    await expect(page.getByTestId("situation-headline")).toContainText("Running");
    await expect(page.getByTestId("situation-body")).toBeVisible();
    await assertSingleCardWithinViewport(page);
  },
);

test(
  "situation card renders the needs_you headline with a read-only decision block",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const taskId = await createWorkspace(request, "e2e-situation-needs-you");
    seedBlockingQuestion(e2eRoot, taskId, "Which storage backend?", {
      status: "awaiting_input",
      proposedAnswerSpace: ["duckdb", "postgres"],
    });

    await page.goto(`/workspaces/${taskId}`);

    await expect(page.getByTestId("situation-headline")).toContainText("Needs you");
    await expect(page.getByTestId("decision-card")).toBeVisible();
    await expect(page.getByRole("radiogroup")).toHaveCount(0);
    await assertSingleCardWithinViewport(page);
  },
);

test(
  "situation card renders the deploy_wait headline and status output for a pending deploy gate",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const taskId = await createWorkspace(request, "e2e-situation-deploy-wait");
    insertDeployVerifyJob(e2eRoot, taskId, Date.now() / 1000 + 3_600);

    await page.goto(`/workspaces/${taskId}`);

    await expect(page.getByTestId("situation-headline")).toContainText(
      "Waiting on deploy verification",
    );
    const body = page.getByTestId("pending-deploy-gate-notice");
    await expect(body).toBeVisible();
    await expect(body).toHaveAttribute("aria-label", "Deploy verification status");
    await assertSingleCardWithinViewport(page);
  },
);
