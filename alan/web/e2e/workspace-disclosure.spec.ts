import type { APIRequestContext, Locator, Page } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";

const DESCRIPTION = "Make the workspace page show what the task is about";
const MAX_TAB_PRESSES = 60;

const COST_BREAKDOWN = [
  {
    key: "team_runs",
    label: "Team & agent runs",
    run_summary: {
      model: "aggregate",
      effort: null,
      system_prompt: "",
      total_cost_usd: 0.0025,
      total_output_tokens: null,
      total_input_tokens: null,
      tool_counts: [],
    },
  },
];

async function seedWorkspace(page: Page, request: APIRequestContext): Promise<string> {
  const create = await request.post("/api/workspaces", {
    data: { title: `e2e-disclosure-${String(Date.now())}`, description: DESCRIPTION },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };
  await page.route(
    (url) => url.pathname === `/api/workspaces/${taskId}`,
    async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as Record<string, unknown>;
      await route.fulfill({
        response,
        json: { ...body, total_cost_usd: 0.0025, cost_breakdown: COST_BREAKDOWN },
      });
    },
  );
  return taskId;
}

async function tabUntilFocused(page: Page, target: Locator): Promise<void> {
  for (let presses = 0; presses < MAX_TAB_PRESSES; presses += 1) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  await expect(target).toBeFocused();
}

test(
  "the task description starts collapsed and opens on click",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const taskId = await seedWorkspace(page, request);
    await page.goto(`/workspaces/${taskId}`);
    const description = page.getByTestId("workspace-task-description");
    const disclosure = page.getByTestId("workspace-task-description-disclosure");
    await expect(description).toBeHidden();
    await expect(disclosure).not.toHaveAttribute("open", "");

    await page.getByText("Task description").click();

    await expect(disclosure).toHaveAttribute("open", "");
    await expect(description).toBeVisible();
    await expect(description).toContainText(DESCRIPTION);
  },
);

test(
  "the cost breakdown starts closed and opens and closes by keyboard alone",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const taskId = await seedWorkspace(page, request);
    await page.goto(`/workspaces/${taskId}`);
    const breakdown = page.getByTestId("workspace-cost-breakdown");
    const summary = breakdown.locator("summary");
    const row = page.getByTestId("run-summary-header-team_runs");
    await expect(summary).toContainText("▸");
    await expect(row).toBeHidden();

    await tabUntilFocused(page, summary);
    await page.keyboard.press("Enter");
    await expect(breakdown).toHaveAttribute("open", "");
    await expect(summary).toContainText("▾");
    await expect(row).toBeVisible();

    await page.keyboard.press("Space");
    await expect(breakdown).not.toHaveAttribute("open", "");
    await expect(summary).toContainText("▸");
    await expect(row).toBeHidden();

    await page.keyboard.press("Space");
    await expect(row).toBeVisible();
  },
);
