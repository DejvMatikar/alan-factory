import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedProjectFeatures } from "./fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

interface RoadmapItemSeed {
  id: string;
  title: string;
}

async function createProject(request: APIRequestContext, title: string): Promise<string> {
  const res = await request.post("/api/projects", { data: { title } });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { id: string };
  return body.id;
}

async function importRoadmap(
  request: APIRequestContext,
  projectId: string,
  markdown: string,
): Promise<RoadmapItemSeed[]> {
  const res = await request.post(`/api/projects/${projectId}/roadmap/import`, {
    data: { markdown },
  });
  expect(res.status()).toBe(201);
  const body = (await res.json()) as { items: RoadmapItemSeed[] };
  return body.items;
}

async function createWorkspaceUnderProject(
  request: APIRequestContext,
  title: string,
  projectId: string,
): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "e2e seed workspace", project_id: projectId },
  });
  expect(res.ok()).toBeTruthy();
  const { task_id: taskId } = (await res.json()) as { task_id: string };
  return taskId;
}

function stampItemDelivered(
  e2eRoot: string,
  taskId: string,
  projectId: string,
  itemId: string,
): void {
  const wsPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId);
  const metaPath = path.join(wsPath, "meta.json");
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
  meta.status = "completed";
  meta.project_id = projectId;
  meta.roadmap_item_id = itemId;
  fs.writeFileSync(metaPath, JSON.stringify(meta), "utf-8");

  const handoversDir = path.join(wsPath, "handovers");
  fs.mkdirSync(handoversDir, { recursive: true });
  const handoverYaml = [
    'schema_version: "1.19"',
    `handover_id: "handover_${taskId}_001"`,
    "handover_type: terminal",
    `task_id: "${taskId}"`,
    "from_team: pr_merge",
    "from_invocation_id: inv_001",
    "iteration: 1",
    'timestamp_utc: "2026-01-01T00:00:00+00:00"',
    "full_synthesis: merged",
    "pr_number: 7",
  ].join("\n");
  fs.writeFileSync(path.join(handoversDir, `handover_${taskId}_001.yaml`), handoverYaml, "utf-8");
}

function seedWorkspaceCost(e2eRoot: string, taskId: string, costUsd: number): void {
  const dir = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "invocations", "inv_001");
  fs.mkdirSync(dir, { recursive: true });
  const workflowYaml = [
    'schema_version: "1.7"',
    "id: inv_001",
    `namespace: "alan.workspace.${taskId}"`,
    "team: implementation",
    "status: success",
    'started_at: "2026-06-13T00:00:00+00:00"',
    'completed_at: "2026-06-13T00:05:00+00:00"',
    "tasks:",
    "  - id: t1",
    "    type: io.alan.LLMStep",
    "    role: implementer",
    "    model: claude-sonnet-5",
    "    status: success",
    `    total_cost_usd: ${String(costUsd)}`,
  ].join("\n");
  fs.writeFileSync(path.join(dir, "workflow.yaml"), workflowYaml, "utf-8");
}

test.describe("Project — next-item propose-mode card", { tag: "@no-claude" }, () => {
  test("shows the recommendation and a one-tap start dispatches exactly the unattended work", async ({
    page,
    request,
    e2eRoot,
  }) => {
    const stamp = String(Date.now());
    const title = `E2E Propose Card ${stamp}`;
    const projectId = await createProject(request, title);
    const items = await importRoadmap(
      request,
      projectId,
      "## Ship item one\nFirst item body.\n\n## Ship item two\nSecond item body.",
    );
    expect(items).toHaveLength(2);
    const [first, second] = items;

    const seedTaskId = await createWorkspaceUnderProject(
      request,
      `e2e-seed-delivered-${stamp}`,
      projectId,
    );
    stampItemDelivered(e2eRoot, seedTaskId, projectId, first!.id);

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();

    const card = page.getByTestId("next-item-proposal");
    await expect(card).toBeVisible({ timeout: 10_000 });
    await expect(card).toContainText(second!.title);

    await page.getByTestId("next-item-proposal-start").click();

    await expect(card).toHaveCount(0, { timeout: 10_000 });

    await expect
      .poll(
        async () => {
          const res = await request.get(`/api/projects/${projectId}`);
          if (!res.ok()) return -1;
          const body = (await res.json()) as { workspaces: { task_id: string }[] };
          return body.workspaces.length;
        },
        { timeout: 10_000, intervals: [200, 500, 1_000] },
      )
      .toBe(2);
  });

  test("shows no recommendation when the project continues automatically", async ({
    page,
    request,
  }) => {
    const stamp = String(Date.now());
    const title = `E2E Auto Continue ${stamp}`;
    const projectId = await createProject(request, title);
    await importRoadmap(request, projectId, "## Only item\nJust one item.");
    const patch = await request.patch(`/api/projects/${projectId}`, {
      data: { autonomy: "auto_continue" },
    });
    expect(patch.ok()).toBeTruthy();

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();
    await expect(page.getByTestId("next-item-proposal")).toHaveCount(0);
    await expect(page.getByTestId("next-item-proposal-none")).toHaveCount(0);
  });

  test("shows no recommendation once the project's budget is exhausted", async ({
    page,
    request,
    e2eRoot,
  }) => {
    const stamp = String(Date.now());
    const title = `E2E Budget Paused ${stamp}`;
    const projectId = await createProject(request, title);
    const items = await importRoadmap(
      request,
      projectId,
      "## Ship item one\nFirst item body.\n\n## Ship item two\nSecond item body.",
    );
    const [first] = items;
    const seedTaskId = await createWorkspaceUnderProject(
      request,
      `e2e-seed-budget-${stamp}`,
      projectId,
    );
    stampItemDelivered(e2eRoot, seedTaskId, projectId, first!.id);
    seedWorkspaceCost(e2eRoot, seedTaskId, 50);

    const patch = await request.patch(`/api/projects/${projectId}`, {
      data: { budget_usd: 10 },
    });
    expect(patch.ok()).toBeTruthy();

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();
    await expect(page.getByTestId("project-budget-pill")).toContainText("Paused");
    await expect(page.getByTestId("next-item-proposal")).toHaveCount(0);
    await expect(page.getByTestId("next-item-proposal-none")).toHaveCount(0);
  });
});
