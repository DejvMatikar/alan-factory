import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";

test(
  "@no-claude manager summary panel renders near top when sidecar is present",
  async ({ page, request, e2eRoot }) => {
    const E2E_WORKSPACES = path.join(e2eRoot, "workspaces");
    const create = await request.post("/api/workspaces", {
      data: { title: "e2e-mgr-summary", description: "test manager summary" },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    fs.writeFileSync(
      path.join(E2E_WORKSPACES, taskId, "tasks", taskId, "manager_summary.json"),
      JSON.stringify({ ok: true, text: "The task completed without issues." }),
    );

    await page.goto(`/workspaces/${taskId}`);

    const panel = page.getByTestId("manager-summary-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("The task completed without issues.");

    const invHeading = page.getByRole("heading", { level: 3, name: "Invocations" });
    await expect(invHeading).toBeVisible();

    const panelBox = await panel.boundingBox();
    const invBox = await invHeading.boundingBox();
    expect(panelBox!.y).toBeLessThan(invBox!.y);
  },
);
