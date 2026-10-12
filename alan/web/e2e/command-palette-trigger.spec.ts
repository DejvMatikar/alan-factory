import type { APIRequestContext } from "@playwright/test";

import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const REPO_OWNER = "e2e-palette-trigger-co";
const REPO_KEY = `${REPO_OWNER}/widget`;

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, REPO_OWNER);
});

async function seedWorkspaceInRepo(request: APIRequestContext): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: {
      title: `e2e-palette-trigger-${String(Date.now())}`,
      description: "command palette trigger e2e",
      engine_key: REPO_KEY,
    },
  });
  expect(res.ok()).toBeTruthy();
  const { task_id: taskId } = (await res.json()) as { task_id: string };
  return taskId;
}

test(
  "the visible Commands control opens the palette on a workspace page, and closing the palette returns focus to it",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const taskId = await seedWorkspaceInRepo(request);
    await page.goto(`/workspaces/${taskId}`);
    const trigger = page.getByTestId("command-palette-trigger");
    await expect(trigger).toBeVisible();
    await expect(trigger).toHaveAccessibleName(/^Commands/);
    await trigger.click();
    await expect(page.getByTestId("command-palette")).toBeVisible();
    await expect(page.getByTestId("palette-command-build")).toBeVisible();
    await expect(page.getByTestId("palette-command-explore")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("command-palette")).toHaveCount(0);
    await expect(trigger).toBeFocused();
  },
);

test(
  "the palette opened from a workspace page offers Build and Explore in that workspace's repository",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const taskId = await seedWorkspaceInRepo(request);
    await page.goto(`/workspaces/${taskId}`);
    await expect(page.getByTestId(`top-bar-repo-${REPO_KEY}`)).toHaveAttribute("aria-current", "true");
    await page.getByTestId("command-palette-trigger").click();
    await expect(page.getByTestId("palette-command-build-scoped")).toContainText("Build in Widget");
    await expect(page.getByTestId("palette-command-explore-scoped")).toContainText("Explore Widget");
  },
);

test(
  "Ctrl+Shift+X on a workspace page opened directly starts a build conversation in that workspace's repository",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const taskId = await seedWorkspaceInRepo(request);
    await page.goto(`/workspaces/${taskId}`);
    await expect(page.getByTestId(`top-bar-repo-${REPO_KEY}`)).toHaveAttribute("aria-current", "true");
    await page.keyboard.press("Control+Shift+X");
    await expect(page).toHaveURL(
      (url) => url.pathname === `/repos/${REPO_KEY}/build` && url.searchParams.get("engine") === REPO_KEY,
    );
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
  },
);

test(
  "Ctrl+Shift+E on a workspace page opened directly opens Explore for that workspace's repository",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const taskId = await seedWorkspaceInRepo(request);
    await page.goto(`/workspaces/${taskId}`);
    await expect(page.getByTestId(`top-bar-repo-${REPO_KEY}`)).toHaveAttribute("aria-current", "true");
    await page.keyboard.press("Control+Shift+E");
    await expect(page.getByTestId("consultant-modal")).toBeVisible();
    await expect(page.getByTestId("command-palette")).toHaveCount(0);
  },
);
