import type { Page } from "@playwright/test";

import { gotoBuildSession } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

const BUILD_MESSAGES_RE = /\/build-chat\/sessions\/[^/]+\/messages$/;

async function holdFirstMessagePost(page: Page): Promise<() => void> {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let firstIntercepted = false;
  await page.route(BUILD_MESSAGES_RE, async (route) => {
    if (firstIntercepted) {
      await route.continue();
      return;
    }
    firstIntercepted = true;
    await held;
    await route.continue();
  });
  return release;
}

test("build chat: second message queued during thinking auto-flushes after turn completes", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await gotoBuildSession(page);

  const composer = page.getByLabel("Message");
  await expect(composer).toBeVisible({ timeout: 15_000 });

  const releaseFirstPost = await holdFirstMessagePost(page);
  try {
    await composer.fill("First message");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("queue-button")).toBeVisible();

    await composer.fill("Second message");
    await page.getByTestId("queue-button").click();
    await expect(page.getByTestId("queued-indicator")).toBeVisible();
  } finally {
    releaseFirstPost();
  }

  await expect(page.getByTestId("queued-indicator")).toBeHidden({ timeout: 30_000 });

  await expect(page.getByTestId("chat-user")).toHaveCount(2, { timeout: 15_000 });
});

test("build chat: cancel button on queued bubble prevents auto-flush", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await gotoBuildSession(page);

  const composer = page.getByLabel("Message");
  await expect(composer).toBeVisible({ timeout: 15_000 });

  const releaseFirstPost = await holdFirstMessagePost(page);
  try {
    await composer.fill("First message");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("queue-button")).toBeVisible();

    await composer.fill("Cancelled message");
    await page.getByTestId("queue-button").click();
    await expect(page.getByTestId("queued-indicator")).toBeVisible();

    await page.getByTestId("queued-cancel").click();
    await expect(page.getByTestId("queued-indicator")).toBeHidden();
  } finally {
    releaseFirstPost();
  }

  await expect(page.getByTestId("thinking")).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId("chat-user")).toHaveCount(1, { timeout: 15_000 });
});
