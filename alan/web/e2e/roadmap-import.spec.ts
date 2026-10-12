import { expect, test } from "./fixtures/test-base";
import { seedProjectFeatures } from "./fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

const ROADMAP_MARKDOWN = `# Q3 roadmap

## Ship the widget

Build the core widget end to end.

## Polish the dashboard

Add charts and empty states.
`;

test(
  "user imports a roadmap through the guided flow and sees it as ordered, not-started items",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Roadmap Import ${String(Date.now())}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const { id: projectId } = (await create.json()) as { id: string };

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();
    await expect(page.getByTestId("project-roadmap-empty")).toBeVisible();

    await page.getByTestId("import-roadmap-open").click();
    await expect(page.getByTestId("import-roadmap-dialog")).toBeVisible();
    await page.getByTestId("import-roadmap-markdown").fill(ROADMAP_MARKDOWN);
    await page.getByTestId("import-roadmap-preview-button").click();

    const previewList = page.getByTestId("import-roadmap-preview-list");
    await expect(previewList).toContainText("Ship the widget");
    await expect(previewList).toContainText("Polish the dashboard");

    await page.getByTestId("import-roadmap-submit").click();
    await expect(page.getByTestId("import-roadmap-dialog")).toHaveCount(0);

    const list = page.getByTestId("project-roadmap-list");
    await expect(list).toBeVisible();
    const items = list.getByRole("listitem");
    await expect(items).toHaveCount(2);
    await expect(items.nth(0)).toContainText("Ship the widget");
    await expect(items.nth(1)).toContainText("Polish the dashboard");
    await expect(items.nth(0).getByTestId("roadmap-status-not_started")).toBeVisible();

    await expect(page.getByTestId("import-roadmap-open")).toHaveCount(0);
    await expect(page.getByTestId("propose-roadmap-changes-open")).toBeVisible();

    const detail = await request.get(`/api/projects/${projectId}`);
    const body = (await detail.json()) as { roadmap: { title: string; status: string }[] };
    expect(body.roadmap.map((r) => r.title)).toEqual([
      "Ship the widget",
      "Polish the dashboard",
    ]);
    expect(body.roadmap.every((r) => r.status === "not_started")).toBe(true);
  },
);

test(
  "reloading the project page after import still shows the same items, never duplicates",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `E2E Roadmap Reimport ${String(Date.now())}`;
    const create = await request.post("/api/projects", { data: { title } });
    expect(create.ok()).toBeTruthy();
    const { id: projectId } = (await create.json()) as { id: string };

    const importRes = await request.post(`/api/projects/${projectId}/roadmap/import`, {
      data: { markdown: ROADMAP_MARKDOWN },
    });
    expect(importRes.status()).toBe(201);

    await page.goto(`/projects/${projectId}`);
    const list = page.getByTestId("project-roadmap-list");
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(page.getByTestId("import-roadmap-open")).toHaveCount(0);

    const reimport = await request.post(`/api/projects/${projectId}/roadmap/import`, {
      data: { markdown: ROADMAP_MARKDOWN },
    });
    expect(reimport.status()).toBe(409);

    await page.reload();
    await expect(page.getByTestId("project-roadmap-list").getByRole("listitem")).toHaveCount(2);
  },
);
