import { openExploreFromHome, settleChatSession } from "./fixtures/helpers";
import { stubDefaultEngineWorld } from "./fixtures/route-stubs";
import { expect, test } from "./fixtures/test-base";

test.describe("explore-chat launch", { tag: "@no-claude" }, () => {
  test.beforeEach(async ({ page }) => {
    await stubDefaultEngineWorld(page);
    await page.goto("/repos/DejvMatikar/alan");
  });

  test("the Explore shortcut opens the consultant modal", async ({ page }) => {
    await openExploreFromHome(page);
    await expect(page.getByTestId("consultant-modal")).toBeVisible();
  });

  test("modal lists all five consultants", async ({ page }) => {
    await openExploreFromHome(page);
    for (const key of [
      "brainstorming",
      "research",
      "architecture",
      "opportunity_scout",
      "ui_review",
    ]) {
      await expect(page.getByTestId(`consultant-${key}`)).toBeVisible();
    }
  });

  test("modal close button dismisses the modal", async ({ page }) => {
    await openExploreFromHome(page);
    await page.getByTestId("modal-close").click();
    await expect(page.getByTestId("consultant-modal")).not.toBeVisible();
  });

  test("clicking a consultant navigates to explore chat", async ({ page }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await expect(page).toHaveURL(/\/explore\/brainstorming/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Brainstorming");
  });

  test("clicking UI review navigates to explore chat", async ({ page }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-ui_review").click();
    await settleChatSession(page);
    await expect(page).toHaveURL(/\/explore\/ui_review/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("UI review");
  });

  test("explore chat shows Chat and Summary tabs", async ({ page }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-architecture").click();
    await settleChatSession(page);
    await expect(page.getByTestId("tab-chat")).toBeVisible();
    await expect(page.getByTestId("tab-summary")).toBeVisible();
  });

  test("Chat/Summary tabs expose tab semantics and support arrow-key navigation", async ({
    page,
  }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-architecture").click();
    await settleChatSession(page);
    await expect(page).toHaveURL(/[?&]s=/);
    const chatTab = page.getByTestId("tab-chat");
    const summaryTab = page.getByTestId("tab-summary");
    await expect(page.getByRole("tablist")).toBeVisible();
    await expect(chatTab).toHaveAttribute("aria-selected", "true");

    await chatTab.focus();
    await expect(chatTab).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(summaryTab).toHaveAttribute("aria-selected", "true");
    await expect(summaryTab).toBeFocused();
  });

  test("Chat transcript no longer creates its own scroll region — the whole page scrolls", async ({
    page,
  }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await expect(page.getByTestId("chat-transcript")).toBeVisible({ timeout: 10_000 });
    const overflowY = await page
      .getByTestId("chat-transcript")
      .evaluate((el) => getComputedStyle(el).overflowY);
    expect(overflowY).not.toBe("auto");
    expect(overflowY).not.toBe("scroll");
  });
});
