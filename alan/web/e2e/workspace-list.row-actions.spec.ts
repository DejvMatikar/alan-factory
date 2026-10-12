import type { APIRequestContext } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

interface MinimalMeta {
  status: string;
  updated_at: string;
  [key: string]: unknown;
}

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-row-actions-blank-slate-guard-co");
});

function setWorkspaceStatus(e2eRoot: string, taskId: string, status: string): void {
  const metaPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "meta.json");
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as MinimalMeta;
  meta.status = status;
  meta.updated_at = new Date().toISOString();
  fs.writeFileSync(metaPath, JSON.stringify(meta), "utf-8");
}

async function createWorkspace(request: APIRequestContext, title: string): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "row-actions e2e" },
  });
  expect(res.ok()).toBeTruthy();
  const { task_id: taskId } = (await res.json()) as { task_id: string };
  return taskId;
}

test(
  "each row shows exactly one action control matched to its status: Stop, Archive, Unarchive",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());
    const activeTitle = `e2e-row-action-active-${stamp}`;
    const completedTitle = `e2e-row-action-completed-${stamp}`;
    const archivedTitle = `e2e-row-action-archived-${stamp}`;
    const activeId = await createWorkspace(request, activeTitle);
    const completedId = await createWorkspace(request, completedTitle);
    setWorkspaceStatus(e2eRoot, completedId, "completed");
    const archivedId = await createWorkspace(request, archivedTitle);
    const archiveRes = await request.post(`/api/workspaces/${archivedId}/archive`);
    expect(archiveRes.status()).toBe(204);

    await page.goto("/");
    const activeRow = page.getByTestId(`home-row-feature-${activeId}`);
    const completedRow = page.getByTestId(`home-row-feature-${completedId}`);
    await expect(activeRow).toBeVisible();
    await expect(completedRow).toBeVisible();

    await expect(activeRow.getByRole("button")).toHaveCount(1);
    await expect(activeRow.getByTestId(`workspace-row-action-${activeId}`)).toHaveAccessibleName(
      `Stop — ${activeTitle}`,
    );
    await expect(
      activeRow.locator("td").first().getByTestId(`workspace-row-action-${activeId}`),
    ).toHaveCount(1);

    await expect(completedRow.getByRole("button")).toHaveCount(1);
    await expect(completedRow.getByTestId(`workspace-row-action-${completedId}`)).toHaveAccessibleName(
      `Archive — ${completedTitle}`,
    );

    await page.getByRole("tab", { name: "Archived" }).click();
    const archivedRow = page.getByTestId(`home-row-feature-${archivedId}`);
    await expect(archivedRow).toBeVisible();
    await expect(archivedRow.getByRole("button")).toHaveCount(1);
    await expect(archivedRow.getByTestId(`workspace-row-action-${archivedId}`)).toHaveAccessibleName(
      `Unarchive — ${archivedTitle}`,
    );

    await expect(page.getByRole("checkbox")).toHaveCount(0);
    await expect(page.getByTestId("bulk-action-bar")).toHaveCount(0);
  },
);

test(
  "Stop fires immediately on click with no confirmation dialog",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-row-action-stop-${stamp}`);

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();
    const stopButton = row.getByTestId(`workspace-row-action-${taskId}`);
    await stopButton.click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await expect
      .poll(async () => {
        const res = await request.get(`/api/workspaces/${taskId}`);
        if (!res.ok()) return null;
        const body = (await res.json()) as { has_running_job: boolean };
        return body.has_running_job;
      })
      .toBe(false);
  },
);

test(
  "the action control's description reveals on hover and on keyboard focus, and Escape dismisses it",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-row-action-described-${stamp}`);

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();
    const button = row.getByTestId(`workspace-row-action-${taskId}`);
    const describedBy = await button.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    const description = page.locator(`[id="${String(describedBy)}"]`);
    await expect(description).toHaveClass(/sr-only/);

    await button.hover();
    await expect(description).not.toHaveClass(/sr-only/);
    await page.mouse.move(0, 0);
    await expect(description).toHaveClass(/sr-only/);

    await button.focus();
    await expect(description).not.toHaveClass(/sr-only/);
    await page.keyboard.press("Escape");
    await expect(description).toHaveClass(/sr-only/);
  },
);

test(
  "archiving from the active tab moves the row to the archived tab, and the network call hits the archive route",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-row-action-archive-${stamp}`);
    setWorkspaceStatus(e2eRoot, taskId, "completed");

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();

    const [archiveRequest] = await Promise.all([
      page.waitForRequest((r) => r.url().includes(`/api/workspaces/${taskId}/archive`) && r.method() === "POST"),
      row.getByTestId(`workspace-row-action-${taskId}`).click(),
    ]);
    expect(archiveRequest.url()).toContain(`/api/workspaces/${taskId}/archive`);

    await expect(page.getByTestId(`home-row-feature-${taskId}`)).toHaveCount(0);
    await page.getByRole("tab", { name: "Archived" }).click();
    await expect(page.getByTestId(`home-row-feature-${taskId}`)).toBeVisible();
  },
);

test(
  "unarchiving from the archived tab moves the row back to the active tab, and the network call hits the unarchive route",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-row-action-unarchive-${stamp}`);
    const archiveRes = await request.post(`/api/workspaces/${taskId}/archive`);
    expect(archiveRes.status()).toBe(204);

    await page.goto("/");
    await page.getByRole("tab", { name: "Archived" }).click();
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();

    const [unarchiveRequest] = await Promise.all([
      page.waitForRequest(
        (r) => r.url().includes(`/api/workspaces/${taskId}/unarchive`) && r.method() === "POST",
      ),
      row.getByTestId(`workspace-row-action-${taskId}`).click(),
    ]);
    expect(unarchiveRequest.url()).toContain(`/api/workspaces/${taskId}/unarchive`);

    await expect(page.getByTestId(`home-row-feature-${taskId}`)).toHaveCount(0);
    await page.getByRole("tab", { name: "Active" }).click();
    await expect(page.getByTestId(`home-row-feature-${taskId}`)).toBeVisible();
  },
);
