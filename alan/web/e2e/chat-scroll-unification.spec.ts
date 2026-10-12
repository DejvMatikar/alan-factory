import fs from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { settleChatSession } from "./fixtures/helpers";
import { stubDefaultEngineWorld, stubDefaultProjectWorld } from "./fixtures/route-stubs";
import { seedProjectFeatures } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

test.use({ viewport: { width: 1440, height: 900 } });

interface Door {
  name: string;
  path: string;
  heading: string;
  sessionsDir: string;
}

const DOORS: Door[] = [
  { name: "Build", path: "/build", heading: "Build", sessionsDir: "build-chat-sessions" },
  {
    name: "Explore",
    path: "/explore/brainstorming",
    heading: "Brainstorming",
    sessionsDir: "explore-chat-sessions",
  },
];

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
  await stubDefaultProjectWorld(page);
  await stubDefaultEngineWorld(page);
});

async function sendMessages(page: Page, count: number): Promise<void> {
  for (let i = 0; i < count; i += 1) {
    await page.getByLabel("Message").fill(`message number ${String(i)}`);
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("chat-user").last()).toHaveText(`message number ${String(i)}`, {
      timeout: 15_000,
    });
    await expect(page.getByTestId("chat-assistant").last()).toBeVisible({ timeout: 15_000 });
  }
}

function seedTranscriptWithOperations(
  e2eRoot: string,
  sessionsDir: string,
  sessionId: string,
  operations: string[],
): void {
  const sessionPath = path.join(e2eRoot, sessionsDir, sessionId, "session.json");
  const session = JSON.parse(fs.readFileSync(sessionPath, "utf-8")) as Record<string, unknown>;
  session.transcript = [
    {
      role: "assistant",
      content: "an earlier reply with recorded activity",
      ts: "2026-05-12T08:00:00Z",
      interrupted: false,
      operations,
    },
    ...(session.transcript as unknown[]),
  ];
  fs.writeFileSync(sessionPath, JSON.stringify(session), "utf-8");
}

for (const door of DOORS) {
  test.describe(door.name, () => {
    test(
      `${door.name}: the heading and tablist leave the viewport on page scroll`,
      { tag: "@no-claude" },
      async ({ page }) => {
        await page.goto(door.path);
        await settleChatSession(page);
        await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });

        const heading = page.getByRole("heading", { level: 1, name: door.heading });
        const tablist = page.getByRole("tablist");
        await expect(heading).toBeVisible();
        await expect(tablist).toBeVisible();

        await sendMessages(page, 6);
        await page.mouse.wheel(0, 4000);
        await expect
          .poll(() => page.evaluate(() => window.scrollY), { timeout: 5_000 })
          .toBeGreaterThan(0);

        await expect(heading).not.toBeInViewport();
        await expect(tablist).not.toBeInViewport();
      },
    );

    test(
      `${door.name}: Jump to latest appears while scrolled up and returns to the bottom on click`,
      async ({ page }) => {
        await page.goto(door.path);
        await settleChatSession(page);
        await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });

        await sendMessages(page, 6);
        const jump = page.getByTestId("chat-jump-to-latest");
        await expect(jump).toHaveCount(0);

        await page.evaluate(() => window.scrollTo(0, 0));
        await expect(jump).toBeVisible({ timeout: 5_000 });
        const box = await jump.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.y + box!.height).toBeLessThanOrEqual(900);
        expect(box!.y).toBeGreaterThan(450);

        await jump.click();
        await expect(jump).toHaveCount(0);
        await expect(page.getByTestId("chat-user").last()).toBeInViewport();
      },
    );

    test(
      `${door.name}: keyboard-only navigation reaches Jump to latest only while it is shown`,
      async ({ page }) => {
        await page.goto(door.path);
        await settleChatSession(page);
        await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
        await sendMessages(page, 6);

        await page.getByLabel("Message").focus();
        await page.keyboard.press("Shift+Tab");
        const beforeShown = await page.evaluate(() =>
          document.activeElement?.getAttribute("data-testid"),
        );
        expect(beforeShown).not.toBe("chat-jump-to-latest");

        await page.getByLabel("Message").focus();
        await page.evaluate(() => window.scrollTo(0, 0));
        await expect(page.getByTestId("chat-jump-to-latest")).toBeVisible({ timeout: 5_000 });

        await page.keyboard.press("Shift+Tab");
        await expect(page.getByTestId("chat-jump-to-latest")).toBeFocused();

        await page.keyboard.press("Enter");
        await expect(page.getByTestId("chat-jump-to-latest")).toHaveCount(0);
      },
    );

    test(
      `${door.name}: expanding an ActivityLine pushes following content down and surfaces Jump to latest`,
      { tag: "@no-claude" },
      async ({ page, e2eRoot }) => {
        await page.goto(door.path);
        await settleChatSession(page);
        await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
        const sessionId = new URL(page.url()).searchParams.get("s");
        expect(sessionId).toBeTruthy();

        const operations = Array.from({ length: 30 }, (_, i) => `Read: file-${String(i)}.py`);
        seedTranscriptWithOperations(e2eRoot, door.sessionsDir, sessionId!, operations);
        await page.reload();
        await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });

        const jump = page.getByTestId("chat-jump-to-latest");
        await expect(jump).toHaveCount(0);

        const composerBefore = await page.getByLabel("Message").boundingBox();
        await page.getByTestId("activity-line-toggle").first().click();
        await expect(page.getByTestId("activity-line-list").first()).toBeVisible();
        const composerAfter = await page.getByLabel("Message").boundingBox();

        expect(composerBefore).not.toBeNull();
        expect(composerAfter).not.toBeNull();
        expect(composerAfter!.y).toBeGreaterThan(composerBefore!.y);
        await expect(jump).toBeVisible({ timeout: 5_000 });
      },
    );
  });
}
