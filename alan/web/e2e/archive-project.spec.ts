import { expect, test } from "./fixtures/test-base";
import { seedProjectFeatures } from "./fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

test(
  "user can archive a project from the project page and reverse it",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Archive Project ${String(Date.now())}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const { id: projectId } = (await create.json()) as { id: string };

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();
    await expect(page.getByTestId("project-status-active")).toBeVisible();

    await page.getByRole("button", { name: "Archive" }).click();
    await expect(page.getByTestId("project-status-archived")).toBeVisible();

    const afterArchive = await request.get(`/api/projects/${projectId}`);
    expect((await afterArchive.json()) as { status: string }).toMatchObject({
      status: "archived",
    });

    await page.getByRole("button", { name: "Unarchive" }).click();
    await expect(page.getByTestId("project-status-active")).toBeVisible();

    const afterUnarchive = await request.get(`/api/projects/${projectId}`);
    expect((await afterUnarchive.json()) as { status: string }).toMatchObject({
      status: "active",
    });
  },
);

test(
  "the archive toggle is not shown for a completed (shipped) project",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Shipped Project ${String(Date.now())}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const { id: projectId } = (await create.json()) as { id: string };

    const complete = await request.post(`/api/projects/${projectId}/complete`, {
      data: { force: true, reason: "e2e force-ship for archive-toggle visibility test" },
    });
    expect(complete.ok()).toBeTruthy();

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();
    await expect(page.getByTestId("project-status-completed")).toBeVisible();
    await expect(page.getByRole("button", { name: "Archive" })).toHaveCount(0);
  },
);
