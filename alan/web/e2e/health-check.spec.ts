import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const DEFAULT_REPO_KEY = "DejvMatikar/alan";

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-health-check-co");
});

test(
  "the repo drawer trigger, nav-health-badge and the default repo's Maintenance control meet the 44px minimum tap target at 390px",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);

    const targets = [
      page.getByTestId("repo-drawer-trigger"),
      page.getByRole("banner").getByTestId("nav-health-badge"),
      page.getByTestId(`repo-action-maintenance-${DEFAULT_REPO_KEY}`),
    ];
    for (const target of targets) {
      await expect(target).toBeVisible();
      await expect
        .poll(async () => {
          const box = await target.boundingBox();
          return box === null ? null : Math.min(box.width, box.height);
        })
        .toBeGreaterThanOrEqual(44);
    }
  },
);

test("The default repo's Maintenance control creates a workspace, dispatches maintenance scoped to that repo, and reports it on the repo page without navigating away", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  await page.goto("/");
  const maintenance = page.getByTestId(`repo-action-maintenance-${DEFAULT_REPO_KEY}`);
  await expect(maintenance).toBeVisible();
  await maintenance.click();

  const notice = page.getByTestId(`repo-maintenance-notice-${DEFAULT_REPO_KEY}`);
  await expect(notice).toBeVisible({ timeout: 10_000 });
  await expect(page).toHaveURL((u) => new URL(u).pathname === `/repos/${DEFAULT_REPO_KEY}`);

  const href = await notice.getByRole("link").getAttribute("href");
  const taskId = decodeURIComponent((href ?? "").split("/workspaces/").at(1) ?? "");
  expect(taskId).not.toBe("");

  const wsRes = await request.get(`/api/workspaces/${taskId}`);
  expect(wsRes.ok()).toBeTruthy();
  const wsBody = (await wsRes.json()) as { engine_key: string | null };
  expect(wsBody.engine_key).toBe(DEFAULT_REPO_KEY);

  await expect
    .poll(
      async () => {
        const res = await request.get(
          `/api/workspaces/${taskId}/invocations/inv_001`,
        );
        if (!res.ok()) return "pending";
        const doc = (await res.json()) as { status: string };
        return doc.status;
      },
      { timeout: 60_000, intervals: [200, 500, 1_000, 2_000] },
    )
    .toMatch(/success|failed/);
});
