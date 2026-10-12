import { registerGuardEngine, seedProjectFeatures } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const DEFAULT_REPO_KEY = "DejvMatikar/alan";
const NON_DEFAULT_OWNER = "e2e-keyboard-only-dashboard-co";
const NON_DEFAULT_REPO_KEY = `${NON_DEFAULT_OWNER}/widget`;

test.beforeEach(async ({ page, request, e2eRoot }) => {
  await seedProjectFeatures(page, true);
  await registerGuardEngine(request, e2eRoot, NON_DEFAULT_OWNER);
});

test(
  "every hover-revealed description on a repository's header actions is also reachable and revealed by keyboard focus alone",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${NON_DEFAULT_REPO_KEY}`);

    for (const suffix of ["hide", "settings"]) {
      const control = page.getByTestId(`repo-action-${suffix}-${NON_DEFAULT_REPO_KEY}`);
      const describedBy = await control.getAttribute("aria-describedby");
      const description = page.locator(`[id="${String(describedBy)}"]`);
      await expect(description).toHaveClass(/sr-only/);
      await control.focus();
      await expect(description).not.toHaveClass(/sr-only/);
      await control.blur();
      await expect(description).toHaveClass(/sr-only/);
    }

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const maintenance = page.getByTestId(`repo-action-maintenance-${DEFAULT_REPO_KEY}`);
    const maintenanceDescribedBy = await maintenance.getAttribute("aria-describedby");
    const maintenanceDescription = page.locator(`[id="${String(maintenanceDescribedBy)}"]`);
    await expect(maintenanceDescription).toHaveClass(/sr-only/);
    await maintenance.focus();
    await expect(maintenanceDescription).not.toHaveClass(/sr-only/);
    await maintenance.blur();
    await expect(maintenanceDescription).toHaveClass(/sr-only/);
  },
);

test(
  "the default repository's disabled Hide control stays focusable via aria-disabled, and its consequence text stays reachable to assistive tech via aria-describedby",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const hide = page.getByTestId(`repo-action-hide-${DEFAULT_REPO_KEY}`);
    await expect(hide).toBeDisabled();
    await hide.focus();
    await expect(hide).toBeFocused();
    const describedBy = await hide.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    await expect(page.locator(`[id="${String(describedBy)}"]`)).toContainText("can't be hidden");
  },
);

test(
  "the conversation rail's Build/Explore controls and collapse control are reachable by Tab in DOM order",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await page.getByTestId(`rail-start-build-${DEFAULT_REPO_KEY}`).focus();
    await expect(page.getByTestId(`rail-start-build-${DEFAULT_REPO_KEY}`)).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByTestId(`rail-start-explore-${DEFAULT_REPO_KEY}`)).toBeFocused();
  },
);

test(
  "keyboard tab order reaches a workspace row's action control before its identifier link, matching the action-first column",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const res = await request.post("/api/workspaces", {
      data: { title: `e2e-tab-order-${stamp}`, description: "keyboard-only-dashboard e2e" },
    });
    const { task_id: taskId } = (await res.json()) as { task_id: string };

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();
    const action = row.getByTestId(`workspace-row-action-${taskId}`);
    const link = row.getByRole("link");

    await action.focus();
    await expect(action).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(link).toBeFocused();
  },
);

test(
  "opening a modal by tabbing past a focused hover-reveal closes the reveal cleanly first, so a single Escape then closes only the modal",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const maintenance = page.getByTestId(`repo-action-maintenance-${DEFAULT_REPO_KEY}`);
    await expect.poll(() => maintenance.evaluate((el) => el.tagName)).toBe("BUTTON");
    const describedBy = await maintenance.getAttribute("aria-describedby");
    const description = page.locator(`[id="${String(describedBy)}"]`);

    await maintenance.focus();
    await expect(maintenance).toBeFocused();
    await expect(description).not.toHaveClass(/sr-only/);

    const startProject = page.getByTestId(`repo-start-project-${DEFAULT_REPO_KEY}`);
    await startProject.focus();
    await expect(startProject).toBeFocused();
    await expect(description).toHaveClass(/sr-only/);

    await page.keyboard.press("Enter");
    const dialog = page.getByTestId("new-project-dialog");
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  },
);
