import type { APIRequestContext } from "@playwright/test";

import { expectRevealAboveSticky } from "./fixtures/layering";
import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-repo-tabs-tooltip-guard-co");
});

async function createWorkspace(request: APIRequestContext, title: string): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "repo-tabs-tooltip e2e" },
  });
  expect(res.ok()).toBeTruthy();
  const { task_id: taskId } = (await res.json()) as { task_id: string };
  return taskId;
}

test(
  "hovering the Active tab reveals its tooltip above the workspace table's sticky column headers, not behind them",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    await createWorkspace(request, `e2e-tab-tooltip-hover-${stamp}`);

    await page.goto("/");
    const tab = page.getByRole("tab", { name: "Active" });
    await expect(tab).toBeVisible();
    const describedBy = await tab.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();

    await expectRevealAboveSticky(page, String(describedBy), "thead th", () => tab.hover());
  },
);

test(
  "keyboard-focusing the Archived tab reveals its tooltip above the workspace table's sticky column headers, not behind them",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    await createWorkspace(request, `e2e-tab-tooltip-focus-${stamp}`);

    await page.goto("/");
    const tab = page.getByRole("tab", { name: "Archived" });
    await expect(tab).toBeVisible();
    const describedBy = await tab.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();

    await expectRevealAboveSticky(page, String(describedBy), "thead th", () => tab.focus());
  },
);
