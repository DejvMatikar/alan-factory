import { openCommandPalette as openPalette, settleChatSession } from "./fixtures/helpers";
import { stubDefaultEngineWorld } from "./fixtures/route-stubs";
import { expect, test } from "./fixtures/test-base";

const DEFAULT_REPO_KEY = "DejvMatikar/alan";

test.describe("command palette", { tag: "@no-claude" }, () => {
  test.beforeEach(async ({ page }) => {
    await stubDefaultEngineWorld(page);
  });

  test("Ctrl+K opens the palette from a route other than Home, filter focused", async ({ page }) => {
    await page.goto("/health");
    await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible();

    await openPalette(page, "Control+k");
    await expect(page.getByTestId("list-chooser-filter")).toBeFocused();
  });

  test("keyboard-only: Ctrl+K, Build, then a repository reaches a build conversation", async ({
    page,
  }) => {
    await page.goto("/");
    await openPalette(page, "Control+k");

    await page.getByTestId("palette-command-build").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Build" })).toBeVisible();
    await expect(page.getByTestId("list-chooser-filter")).toBeFocused();

    await page.getByTestId(`palette-repo-${DEFAULT_REPO_KEY}`).focus();
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("command-palette")).toHaveCount(0);
    await expect(page).toHaveURL(/\/build/);
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
  });

  test("keyboard-only: Ctrl+K, Explore, a consultant, then a repository reaches an explore conversation", async ({
    page,
  }) => {
    await page.goto("/");
    await openPalette(page, "Control+k");
    await page.getByTestId("palette-command-explore").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Explore" })).toBeVisible();

    await page.getByTestId("palette-consultant-research").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Explore / Research" })).toBeVisible();
    await expect(page.getByTestId("list-chooser-filter")).toBeFocused();

    await page.getByTestId(`palette-repo-${DEFAULT_REPO_KEY}`).focus();
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("command-palette")).toHaveCount(0);
    await expect(page).toHaveURL(/\/explore\/research/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Research");
  });

  test("Ctrl+Alt+B jumps straight to the build-repo step, skipping the root menu", async ({
    page,
  }) => {
    await page.goto("/");
    await openPalette(page, "Control+Alt+B");
    await expect(page.getByRole("heading", { name: "Build" })).toBeVisible();
    await expect(page.getByTestId("palette-command-build")).toHaveCount(0);

    await page.getByTestId(`palette-repo-${DEFAULT_REPO_KEY}`).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/build/);
  });

  test("Ctrl+Alt+C jumps straight to the explore-consultant step, skipping the root menu", async ({
    page,
  }) => {
    await page.goto("/");
    await openPalette(page, "Control+Alt+C");
    await expect(page.getByRole("heading", { name: "Explore" })).toBeVisible();
    await expect(page.getByTestId("palette-command-explore")).toHaveCount(0);

    await page.getByTestId("palette-consultant-brainstorming").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Explore / Brainstorming" })).toBeVisible();
  });

  test("Escape closes the palette and returns focus to the previously focused control", async ({
    page,
  }) => {
    await page.goto("/");
    const anchor = page.getByTestId("settings-gear");
    await anchor.focus();
    await expect(anchor).toBeFocused();

    await openPalette(page, "Control+k");

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("command-palette")).not.toBeVisible();
    await expect(anchor).toBeFocused();
  });

  test("the shortcut stays inert while a text field is focused", async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    const textarea = page.getByLabel("Message");
    await textarea.click();
    await textarea.fill("writing a message");

    await page.keyboard.press("Control+k");
    await expect(page.getByTestId("command-palette")).toHaveCount(0);
  });

  test("the shortcut stays inert while another dialog is open", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("settings-gear").click();
    await expect(page.getByTestId("settings-modal")).toBeVisible();

    await page.keyboard.press("Control+k");
    await expect(page.getByTestId("command-palette")).toHaveCount(0);
    await expect(page.getByTestId("settings-modal")).toBeVisible();
  });
});
