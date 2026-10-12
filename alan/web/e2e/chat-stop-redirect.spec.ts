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

async function assertNoConflictAlert(page: Page): Promise<void> {
  const alerts = page.getByRole("alert");
  const count = await alerts.count();
  for (let i = 0; i < count; i += 1) {
    await expect(alerts.nth(i)).not.toContainText(/409|already in progress|conflict/i);
  }
}

async function stopThenRedirectCycle(
  page: Page,
  e2eRoot: string,
  opts: { messageLabel: string; stopTestId: string; sendLabel: string },
): Promise<void> {
  const hangFile = path.join(e2eRoot, "stub-hang");
  const marker = `${opts.messageLabel}-${String(Date.now())}-${String(Math.floor(Math.random() * 1e6))}`;
  ensureHangFile(hangFile, marker);
  try {
    const composer = page.getByLabel(opts.sendLabel);
    await composer.fill(marker);
    await page.getByRole("button", { name: /Send/ }).click();

    const stopButton = page.getByTestId(opts.stopTestId);
    await expect(stopButton).toBeVisible({ timeout: 15_000 });
    await stopButton.click();

    await expect(stopButton).toBeHidden({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: /Send/ })).toBeVisible();
    await assertNoConflictAlert(page);

    await expect(page.getByTestId("chat-interrupted-label").last()).toBeVisible();
  } finally {
    clearHangFile(hangFile);
  }

  const redirect = `redirect-${String(Date.now())}`;
  await page.getByLabel(opts.sendLabel).fill(redirect);
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByTestId("chat-user").last()).toHaveText(redirect, {
    timeout: 10_000,
  });
  await assertNoConflictAlert(page);
}

test.describe("chat stop-then-redirect", () => {
  test("Build chat: stop then redirect works repeatedly, with no conflict", async ({
    page,
    e2eRoot,
  }) => {
    test.setTimeout(90_000);
    await gotoBuildSession(page);
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });

    await stopThenRedirectCycle(page, e2eRoot, {
      messageLabel: "build-stop-redirect",
      stopTestId: "stop-button",
      sendLabel: "Message",
    });
    await stopThenRedirectCycle(page, e2eRoot, {
      messageLabel: "build-stop-redirect-again",
      stopTestId: "stop-button",
      sendLabel: "Message",
    });
  });

  test("Explore chat: stop then redirect works repeatedly, with no conflict", async ({
    page,
    request,
    e2eRoot,
  }) => {
    test.setTimeout(90_000);
    await registerGuardEngine(request, e2eRoot, "e2e-chat-stop-redirect-co", "stop-redirect-spare");
    await page.goto("/");
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });

    await stopThenRedirectCycle(page, e2eRoot, {
      messageLabel: "explore-stop-redirect",
      stopTestId: "stop-button",
      sendLabel: "Message",
    });
    await stopThenRedirectCycle(page, e2eRoot, {
      messageLabel: "explore-stop-redirect-again",
      stopTestId: "stop-button",
      sendLabel: "Message",
    });
  });

  test("Build chat: a message queued while thinking is restored to the composer on stop, not auto-sent", async ({
    page,
    e2eRoot,
  }) => {
    const hangFile = path.join(e2eRoot, "stub-hang");
    const marker = `build-queue-then-stop-${String(Date.now())}`;
    ensureHangFile(hangFile, marker);
    try {
      await gotoBuildSession(page);
      const composer = page.getByLabel("Message");
      await expect(composer).toBeVisible({ timeout: 10_000 });
      await composer.fill(marker);
      await page.getByRole("button", { name: /Send/ }).click();

      const stopButton = page.getByTestId("stop-button");
      await expect(stopButton).toBeVisible({ timeout: 15_000 });

      const queuedText = "queued while thinking";
      await composer.fill(queuedText);
      await page.getByTestId("queue-button").click();
      await expect(page.getByTestId("queued-indicator")).toBeVisible();

      await stopButton.click();
      await expect(stopButton).toBeHidden({ timeout: 15_000 });

      await expect(page.getByTestId("queued-indicator")).toBeHidden();
      await expect(composer).toHaveValue(queuedText);
      await expect(page.getByTestId("chat-user")).toHaveCount(1);
      await assertNoConflictAlert(page);
    } finally {
      clearHangFile(hangFile);
    }
  });
});
