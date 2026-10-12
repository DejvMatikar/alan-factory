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
  "opens the delete dialog from the header, shows the Ctrl+Enter hint, and Escape cancels without navigating",
  async ({ page, request, e2eRoot }) => {
    const hangFile = path.join(e2eRoot, "stub-hang");
    const title = `E2E HeaderActionsDelete ${String(Date.now())}`;
    let taskId: string | undefined;
    ensureHangFile(hangFile, title);
    try {
      const create = await request.post("/api/workspaces", {
        data: { title, description: "header actions delete-dialog e2e" },
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

      await page.getByTestId("delete-button").click();
      const dialog = page.getByTestId("delete-dialog");
      await expect(dialog).toBeVisible();

      const confirm = dialog.getByRole("button", { name: /^Delete workspace/ });
      await expect(confirm).toHaveAccessibleName(/shortcut Control Enter/);
      const hintText = (await confirm.textContent()) ?? "";
      expect(/⌃↵|Ctrl Enter/.test(hintText)).toBe(true);

      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      expect(new URL(page.url()).pathname).toBe(`/workspaces/${taskId}`);
    } finally {
      if (taskId !== undefined) {
        await request.post(`/api/workspaces/${taskId}/stop`).catch(() => undefined);
      }
      clearHangFile(hangFile);
    }
  },
);

test(
  "the Archive control's description reveals on keyboard focus and is not screen-reader-only while revealed",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const title = `e2e-header-actions-archive-described-${stamp}`;
    const create = await request.post("/api/workspaces", {
      data: { title, description: "header actions archive-described e2e" },
    });
    expect(create.status()).toBe(201);
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    await page.goto(`/workspaces/${taskId}`);
    await expect(
      page.getByRole("heading", { level: 1, name: title }),
    ).toBeVisible();

    const archive = page.getByTestId("archive-button");
    await archive.focus();

    const describedBy = await archive.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    const description = page.locator(`[id="${String(describedBy)}"]`);
    await expect(description).not.toHaveClass(/sr-only/);
    await expect(description).toContainText(
      "Moves this workspace out of the active list.",
    );
  },
);
