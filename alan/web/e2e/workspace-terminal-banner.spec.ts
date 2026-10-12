import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";

function workspacesDir(root: string): string {
  return path.join(root, "workspaces");
}

test(
  "situation card shows the failure reason for a failed workspace",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const E2E_WORKSPACES = workspacesDir(e2eRoot);
    const create = await request.post("/api/workspaces", {
      data: { title: "e2e-terminal-failed", description: "test terminal outcome banner" },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    const metaPath = path.join(E2E_WORKSPACES, taskId, "tasks", taskId, "meta.json");
    const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
    meta.status = "failed";
    fs.writeFileSync(metaPath, JSON.stringify(meta));

    await page.goto(`/workspaces/${taskId}`);

    const card = page.getByTestId("situation-card");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("situation-headline")).toContainText("Failed");
  },
);

test(
  "situation card shows the escalation headline for an escalated workspace",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const E2E_WORKSPACES = workspacesDir(e2eRoot);
    const create = await request.post("/api/workspaces", {
      data: { title: "e2e-terminal-escalated", description: "test terminal outcome escalated" },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    const metaPath = path.join(E2E_WORKSPACES, taskId, "tasks", taskId, "meta.json");
    const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
    meta.status = "escalated";
    fs.writeFileSync(metaPath, JSON.stringify(meta));

    await page.goto(`/workspaces/${taskId}`);

    const card = page.getByTestId("situation-card");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("situation-headline")).toContainText("Escalated");
  },
);
