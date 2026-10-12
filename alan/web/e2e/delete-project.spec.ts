import { expect, test } from "./fixtures/test-base";
import { seedProjectFeatures } from "./fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

test(
  "delete is blocked while a workspace still belongs to the project, with an actionable hint",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Blocked Delete Project ${String(Date.now())}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const { id: projectId } = (await create.json()) as { id: string };

    const wsTitle = `E2E Blocked Delete WS ${String(Date.now())}`;
    const wsCreate = await request.post("/api/workspaces", {
      data: { title: wsTitle, description: "keeps the project referenced", project_id: projectId },
    });
    expect(wsCreate.ok()).toBeTruthy();

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();

    const deleteButton = page.getByTestId("project-delete-button");
    await expect(deleteButton).toBeDisabled();
    await expect(page.getByTestId("project-delete-blocked-hint")).toContainText(
      "Archive it instead, or remove/detach its workspaces",
    );
    await expect(page.getByTestId("delete-project-dialog")).toHaveCount(0);

    const stillThere = await request.get(`/api/projects/${projectId}`);
    expect(stillThere.ok()).toBeTruthy();
  },
);

test(
  "user can permanently delete a project once no workspaces reference it, after confirming in the dialog",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Delete Project ${String(Date.now())}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const { id: projectId } = (await create.json()) as { id: string };

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();

    const deleteButton = page.getByTestId("project-delete-button");
    await expect(deleteButton).toBeEnabled();
    await deleteButton.click();

    const dialog = page.getByTestId("delete-project-dialog");
    await expect(dialog).toBeVisible();

    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toHaveCount(0);

    await deleteButton.click();
    await expect(dialog).toBeVisible();
    await page.getByTestId("delete-project-confirm").click();

    await page.waitForURL("/projects");
    const after = await request.get(`/api/projects/${projectId}`);
    expect(after.status()).toBe(404);
  },
);
