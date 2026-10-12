import type { Page } from "@playwright/test";
import path from "node:path";

import {
  clearHangFile,
  ensureHangFile,
  gotoBuildSession,
  openExploreFromHome,
  settleChatSession,
} from "./fixtures/helpers";
import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

async function stopATurn(page: Page, e2eRoot: string, label: string): Promise<void> {
  const hangFile = path.join(e2eRoot, "stub-hang");
  ensureHangFile(hangFile, `${label}-${String(Date.now())}`);
  try {
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await page.getByLabel("Message").fill(`${label}-${String(Date.now())}`);
    await page.getByRole("button", { name: /Send/ }).click();

    const stopButton = page.getByTestId("stop-button");
    await expect(stopButton).toBeVisible({ timeout: 15_000 });
    await stopButton.click();
    await expect(stopButton).toBeHidden({ timeout: 15_000 });
  } finally {
    clearHangFile(hangFile);
  }
}

test.describe("chat stopped label survives a reload", () => {
  test("Build chat: the Stopped label shows after a stop and again after a reload", async ({
    page,
    e2eRoot,
  }) => {
    test.setTimeout(90_000);
    await gotoBuildSession(page);
    await stopATurn(page, e2eRoot, "build-stopped-reload");
    await expect(page.getByTestId("chat-interrupted-label").last()).toBeVisible();

    await page.reload();
    await expect(page.getByTestId("chat-interrupted-label").last()).toBeVisible({
      timeout: 15_000,
    });
  });

  test("Explore chat: the Stopped label shows after a stop and again after a reload", async ({
    page,
    request,
    e2eRoot,
  }) => {
    test.setTimeout(90_000);
    await registerGuardEngine(request, e2eRoot, "e2e-chat-stopped-label-co", "stopped-label-spare");
    await page.goto("/");
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await stopATurn(page, e2eRoot, "explore-stopped-reload");
    await expect(page.getByTestId("chat-interrupted-label").last()).toBeVisible();

    await page.reload();
    await expect(page.getByTestId("chat-interrupted-label").last()).toBeVisible({
      timeout: 15_000,
    });
  });
});
