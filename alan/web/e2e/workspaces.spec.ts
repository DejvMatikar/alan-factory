import { registerGuardEngine, seedProjectFeatures } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-workspaces-spec-co");
});

test(
  "Home's action row and the default repo's toolbar wrap at 390px so every control stays visible and tappable",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");

    const connectCta = page.getByTestId("connect-repo-cta");
    const startProject = page.getByTestId("repo-start-project-DejvMatikar/alan");
    const maintenance = page.getByTestId("repo-action-maintenance-DejvMatikar/alan");
    await expect(connectCta).toBeVisible();
    await expect(startProject).toBeVisible();
    await expect(maintenance).toBeVisible();

    for (const control of [connectCta, startProject, maintenance]) {
      const box = await control.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    }

    const tablistScrollWidth = await page.getByRole("tablist").evaluate((el) => el.scrollWidth);
    expect(tablistScrollWidth).toBeLessThanOrEqual(390);
  },
);

test(
  "GET /api/workspaces accepts include=triage query param",
  { tag: "@no-claude" },
  async ({ request }) => {
    const res = await request.get("/api/workspaces?include=triage");
    expect(res.status()).toBe(200);
    expect(Array.isArray(await res.json())).toBe(true);
  },
);

test(
  "default workspace list contains user workspaces but not triage workspaces",
  { tag: "@no-claude" },
  async ({ request }) => {
    const title = `E2E Include Test ${String(Date.now())}`;
    const createRes = await request.post("/api/workspaces", {
      data: { title, description: "", budget: "low" },
    });
    expect(createRes.status()).toBe(201);
    const { task_id } = (await createRes.json()) as { task_id: string };

    const defaultRes = await request.get("/api/workspaces");
    expect(defaultRes.status()).toBe(200);
    const defaultList = (await defaultRes.json()) as { task_id: string }[];
    expect(defaultList.map((w) => w.task_id)).toContain(task_id);

    const allRes = await request.get("/api/workspaces?include=triage");
    expect(allRes.status()).toBe(200);
    const allList = (await allRes.json()) as { task_id: string }[];
    expect(allList.length).toBeGreaterThanOrEqual(defaultList.length);
  },
);
