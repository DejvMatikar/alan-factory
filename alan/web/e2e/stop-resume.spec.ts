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

test(
  "user can stop a running workspace, see the paused badge, then resume",
  async ({ page, request, e2eRoot }) => {
    const HANG_FILE = path.join(e2eRoot, "stub-hang");
    const title = `E2E Stop ${String(Date.now())}`;
    let taskId: string | undefined;
    ensureHangFile(HANG_FILE, title);
    try {
      const create = await request.post("/api/workspaces", {
        data: { title, description: "stop me", budget: "fast" },
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
      await expect(page.getByTestId("resume-button")).toBeVisible();

      clearHangFile(HANG_FILE);

      await page.getByTestId("resume-button").click();
      await expect(page.getByTestId("status-paused")).toHaveCount(0, { timeout: 30_000 });
    } finally {
      if (fs.existsSync(HANG_FILE)) {
        if (taskId !== undefined) {
          await request.post(`/api/workspaces/${taskId}/stop`).catch(() => undefined);
        }
        clearHangFile(HANG_FILE);
      }
    }
  },
);
