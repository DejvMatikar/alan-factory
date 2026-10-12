import { openExploreFromHome } from "./fixtures/helpers";
import { stubDefaultEngineWorld } from "./fixtures/route-stubs";
import { expect, test } from "./fixtures/test-base";

const NARROW_VIEWPORT = { width: 390, height: 844 };

test.describe(
  "consultant-picker modal keyboard behaviour (Explore modal-close convention parity)",
  { tag: "@no-claude" },
  () => {
    test.beforeEach(async ({ page }) => {
      await stubDefaultEngineWorld(page);
    });

    test("Escape closes the modal and returns focus to the previously focused control — desktop", async ({
      page,
    }) => {
      await page.goto("/repos/DejvMatikar/alan");
      const anchor = page.getByTestId("settings-gear");
      await anchor.focus();
      await expect(anchor).toBeFocused();
      await openExploreFromHome(page);
      await expect(page.getByTestId("consultant-modal")).toBeVisible();

      await page.keyboard.press("Escape");
      await expect(page.getByTestId("consultant-modal")).not.toBeVisible();
      await expect(anchor).toBeFocused();
    });

    test("Escape closes the modal and returns focus to the previously focused control — 390px", async ({
      page,
    }) => {
      await page.setViewportSize(NARROW_VIEWPORT);
      await page.goto("/repos/DejvMatikar/alan");
      const anchor = page.getByTestId("settings-gear");
      await anchor.focus();
      await expect(anchor).toBeFocused();
      await openExploreFromHome(page);
      await expect(page.getByTestId("consultant-modal")).toBeVisible();

      await page.keyboard.press("Escape");
      await expect(page.getByTestId("consultant-modal")).not.toBeVisible();
      await expect(anchor).toBeFocused();
    });

    test("keyboard focus stays inside the modal while it is open — desktop", async ({ page }) => {
      await page.goto("/repos/DejvMatikar/alan");
      await openExploreFromHome(page);
      const dialog = page.getByTestId("consultant-modal");
      await expect(dialog).toBeVisible();

      const focusedOutsideOnARealElement = () =>
        page.evaluate(() => {
          const active = document.activeElement;
          if (active === null || active === document.body) return false;
          const dialogEl = document.querySelector('[data-testid="consultant-modal"]');
          return !(dialogEl?.contains(active) ?? false);
        });

      for (let i = 0; i < 10; i++) {
        await page.keyboard.press("Tab");
        expect(await focusedOutsideOnARealElement()).toBe(false);
      }
    });

    test("backdrop click closes the modal — desktop", async ({ page }) => {
      await page.goto("/repos/DejvMatikar/alan");
      await openExploreFromHome(page);
      const dialog = page.getByTestId("consultant-modal");
      await expect(dialog).toBeVisible();
      await dialog.click({ position: { x: 2, y: 2 } });
      await expect(dialog).not.toBeVisible();
    });

    test("a single click on a consultant launches it immediately — desktop", async ({ page }) => {
      await page.goto("/repos/DejvMatikar/alan");
      await openExploreFromHome(page);
      await page.getByTestId("consultant-research").click();
      await expect(page).toHaveURL(/\/explore\/research/);
    });

    test("existing keyboard select/launch flow still works — desktop", async ({ page }) => {
      await page.goto("/repos/DejvMatikar/alan");
      await openExploreFromHome(page);
      await page.getByTestId("consultant-architecture").focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/\/explore\/architecture/);
    });

    test("existing keyboard select/launch flow still works — 390px", async ({ page }) => {
      await page.setViewportSize(NARROW_VIEWPORT);
      await page.goto("/repos/DejvMatikar/alan");
      await openExploreFromHome(page);
      await page.getByTestId("consultant-architecture").focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/\/explore\/architecture/);
    });
  },
);
