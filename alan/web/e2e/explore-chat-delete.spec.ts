import path from "node:path";

import {
  clearHangFile,
  ensureHangFile,
  openExploreFromHome,
  settleChatSession,
} from "./fixtures/helpers";
import { stubDefaultEngineWorld } from "./fixtures/route-stubs";
import { expect, test } from "./fixtures/test-base";

test.describe("explore-chat delete", { tag: "@no-claude" }, () => {
  test.beforeEach(async ({ page }) => {
    await stubDefaultEngineWorld(page);
    await page.goto("/repos/DejvMatikar/alan");
  });

  test("declining in-chat delete leaves the chat exactly as it was", async ({ page }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await page.getByLabel("Message").fill("keep this chat");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("chat-user")).toHaveText("keep this chat", { timeout: 15_000 });

    await page.getByTestId("delete-chat-button").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();

    await page.reload();
    await expect(page.getByTestId("chat-user")).toHaveText("keep this chat", { timeout: 15_000 });
    await expect(page.getByTestId("tab-chat")).toHaveAttribute("aria-selected", "true");
  });

  test(
    "confirming in-chat delete lands on home and removes the chat, minting no new draft session",
    async ({ page, request }) => {
      await openExploreFromHome(page);
      await page.getByTestId("consultant-brainstorming").click();
      await settleChatSession(page);
      await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
      await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
      const sessionId = new URL(page.url()).searchParams.get("s");
      expect(sessionId).toBeTruthy();

      const before = await request.get("/api/explore-chat/sessions");
      const beforeCount = ((await before.json()) as unknown[]).length;

      await page.getByTestId("delete-chat-button").click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("button", { name: "Delete chat" }).click();
      await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan", {
        timeout: 15_000,
      });

      const after = await request.get("/api/explore-chat/sessions");
      const afterList = (await after.json()) as { session_id: string }[];
      expect(afterList.some((s) => s.session_id === sessionId)).toBe(false);
      expect(afterList.length).toBe(beforeCount - 1);
    },
  );

  test(
    "deleting mid-stream stops the run first, then removes the chat with no lingering activity",
    async ({ page, e2eRoot, request }) => {
      const hangFile = path.join(e2eRoot, "stub-hang");
      const marker = `explore-delete-mid-stream-${String(Date.now())}`;
      ensureHangFile(hangFile, marker);
      let sessionId: string | null = null;
      try {
        await openExploreFromHome(page);
        await page.getByTestId("consultant-brainstorming").click();
        await settleChatSession(page);
        await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
        await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
        sessionId = new URL(page.url()).searchParams.get("s");
        await page.getByLabel("Message").fill(marker);
        await page.getByRole("button", { name: /Send/ }).click();
        await expect(page.getByTestId("stop-button")).toBeVisible({ timeout: 15_000 });

        await page.getByTestId("delete-chat-button").click();
        const dialog = page.getByRole("dialog");
        await dialog.getByRole("button", { name: "Delete chat" }).click();
        await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan", {
        timeout: 15_000,
      });
      } finally {
        clearHangFile(hangFile);
      }

      const after = await request.get("/api/explore-chat/sessions");
      const afterList = (await after.json()) as { session_id: string }[];
      expect(afterList.some((s) => s.session_id === sessionId)).toBe(false);
    },
  );

  test("a failed in-chat delete keeps the person in the chat with an inline error", async ({
    page,
  }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
    const sessionId = new URL(page.url()).searchParams.get("s");

    await page.route(
      new RegExp(`/api/explore-chat/sessions/${String(sessionId)}$`),
      async (route) => {
        if (route.request().method() !== "DELETE") {
          await route.continue();
          return;
        }
        await route.fulfill({ status: 500, json: { detail: "delete failed" } });
      },
    );

    await page.getByTestId("delete-chat-button").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Delete chat" }).click();

    await expect(page.getByText(/delete failed/)).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(
      new RegExp(`/explore/brainstorming\\?(.*&)?s=${String(sessionId)}`),
    );
    await expect(page.getByTestId("delete-chat-button")).toBeEnabled({ timeout: 15_000 });
  });
});
