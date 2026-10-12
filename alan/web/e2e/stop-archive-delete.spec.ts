import type { APIRequestContext } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";

function ensureHangFile(hangFile: string, title: string) {
  fs.mkdirSync(path.dirname(hangFile), { recursive: true });
  fs.writeFileSync(hangFile, title);
}

function clearHangFile(hangFile: string) {
  try {
    fs.unlinkSync(hangFile);
  } catch {
  }
}

async function stopBestEffort(
  request: APIRequestContext,
  taskId: string | undefined,
) {
  if (taskId !== undefined) {
    await request.post(`/api/workspaces/${taskId}/stop`).catch(() => undefined);
  }
}

test(
  "stopped workspace can be archived from the UI",
  async ({ page, request, e2eRoot }) => {
    const hangFile = path.join(e2eRoot, "stub-hang");
    const title = `E2E StopArchive ${String(Date.now())}`;
    let taskId: string | undefined;
    ensureHangFile(hangFile, title);
    try {
      const create = await request.post("/api/workspaces", {
        data: { title, description: "stop then archive" },
      });
      expect(create.status()).toBe(201);
      ({ task_id: taskId } = (await create.json()) as { task_id: string });

      const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
        data: { team: "research" },
      });
      expect(dispatch.status()).toBe(202);

      await page.goto(`/workspaces/${taskId}`);
      await expect(
        page.getByRole("heading", { level: 1, name: title }),
      ).toBeVisible();

      const stopButton = page.getByTestId("stop-button");
      await expect(stopButton).toBeVisible({ timeout: 30_000 });
      await stopButton.click();
      await expect(page.getByTestId("status-paused")).toBeVisible({ timeout: 30_000 });

      await page.getByRole("button", { name: "Archive" }).click();
      await expect(page.getByTestId("status-archived")).toBeVisible({ timeout: 10_000 });

      const detail = await request.get(`/api/workspaces/${taskId}`);
      const ws = (await detail.json()) as { status: string };
      expect(ws.status).toBe("archived");
    } finally {
      await stopBestEffort(request, taskId);
      clearHangFile(hangFile);
    }
  },
);

test(
  "stopped workspace can be deleted from the UI",
  async ({ page, request, e2eRoot }) => {
    const hangFile = path.join(e2eRoot, "stub-hang");
    const title = `E2E StopDelete ${String(Date.now())}`;
    let taskId: string | undefined;
    ensureHangFile(hangFile, title);
    try {
      const create = await request.post("/api/workspaces", {
        data: { title, description: "stop then delete" },
      });
      expect(create.status()).toBe(201);
      ({ task_id: taskId } = (await create.json()) as { task_id: string });

      const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
        data: { team: "research" },
      });
      expect(dispatch.status()).toBe(202);

      await page.goto(`/workspaces/${taskId}`);
      await expect(
        page.getByRole("heading", { level: 1, name: title }),
      ).toBeVisible();

      const stopButton = page.getByTestId("stop-button");
      await expect(stopButton).toBeVisible({ timeout: 30_000 });
      await stopButton.click();
      await expect(page.getByTestId("status-paused")).toBeVisible({ timeout: 30_000 });

      await page.getByRole("button", { name: "Delete" }).click();
      const dialog = page.getByTestId("delete-dialog");
      await expect(dialog).toBeVisible();
      await page.getByRole("button", { name: "Delete workspace" }).click();

      await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan", {
        timeout: 30_000,
      });
      const detail = await request.get(`/api/workspaces/${taskId}`);
      expect(detail.status()).toBe(404);
    } finally {
      await stopBestEffort(request, taskId);
      clearHangFile(hangFile);
    }
  },
);

test(
  "delete refuses while a job is still running, then succeeds once stopped",
  async ({ page, request, e2eRoot }) => {
    test.setTimeout(180_000);
    const hangFile = path.join(e2eRoot, "stub-hang");
    const title = `E2E StopDeleteBlocked ${String(Date.now())}`;
    let taskId: string | undefined;
    ensureHangFile(hangFile, title);
    try {
      const create = await request.post("/api/workspaces", {
        data: { title, description: "delete while running, then after stop" },
      });
      expect(create.status()).toBe(201);
      ({ task_id: taskId } = (await create.json()) as { task_id: string });

      const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
        data: { team: "research" },
      });
      expect(dispatch.status()).toBe(202);

      await page.goto(`/workspaces/${taskId}`);
      await expect(
        page.getByRole("heading", { level: 1, name: title }),
      ).toBeVisible();

      const stopButton = page.getByTestId("stop-button");
      await expect(stopButton).toBeVisible({ timeout: 30_000 });

      await page.getByRole("button", { name: "Delete" }).click();
      const dialog = page.getByTestId("delete-dialog");
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Delete workspace" }).click();

      const blockedAlert = dialog.getByRole("alert");
      await expect(blockedAlert).toBeVisible({ timeout: 15_000 });
      await expect(blockedAlert).toContainText("wait a moment and retry");
      await expect(page).toHaveURL(new RegExp(`/workspaces/${taskId}$`));
      const stillThere = await request.get(`/api/workspaces/${taskId}`);
      expect(stillThere.status()).toBe(200);

      await dialog.getByRole("button", { name: "Cancel" }).click();
      await expect(dialog).toBeHidden();

      await stopButton.click();
      await expect(page.getByTestId("status-paused")).toBeVisible({ timeout: 30_000 });

      await page.getByRole("button", { name: "Delete" }).click();
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Delete workspace" }).click();

      await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan", {
        timeout: 15_000,
      });
      const detail = await request.get(`/api/workspaces/${taskId}`);
      expect(detail.status()).toBe(404);
    } finally {
      await stopBestEffort(request, taskId);
      clearHangFile(hangFile);
    }
  },
);
