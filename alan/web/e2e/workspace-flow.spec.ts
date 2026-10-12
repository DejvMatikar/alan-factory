import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

test("body uses the dark surface tokens, not the legacy light classes", { tag: "@no-claude" }, async ({ page }) => {
  await page.goto("/");
  const body = page.locator("body");
  await expect(body).toHaveClass(/\bbg-surface\b/);
  await expect(body).toHaveClass(/\btext-text-primary\b/);
  await expect(body).not.toHaveClass(/\bbg-slate-50\b/);
});

test("user can delete a workspace from the detail page", { tag: "@no-claude" }, async ({
  page,
  request,
  e2eRoot,
}) => {
  await registerGuardEngine(request, e2eRoot, "e2e-workspace-flow-co", "workspace-flow-spare");
  const title = `E2E Delete ${String(Date.now())}`;
  const res = await request.post("/api/workspaces", {
    data: { title, description: "delete me", budget: "medium" },
  });
  expect(res.status()).toBe(201);
  const body = (await res.json()) as { task_id: string };
  await page.goto(`/workspaces/${body.task_id}`);
  await expect(
    page.getByRole("heading", { level: 1, name: title }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Delete" }).click();
  const dialog = page.getByTestId("delete-dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Delete workspace" }).click();
  await page.waitForURL(/\/$/);
  await expect(page.getByRole("tab", { name: "Active" })).toBeVisible();
  await expect(page.getByText(title)).toHaveCount(0);
});

test("workspace detail page shows the server-derived total and its cost breakdown", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const create = await request.post("/api/workspaces", {
    data: {
      title: `e2e-cost-${String(Date.now())}`,
      description: "playwright drove this",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: { team: "research" },
  });
  expect(dispatch.ok()).toBeTruthy();

  await expect
    .poll(
      async () => {
        const res = await request.get(`/api/workspaces/${taskId}/invocations/inv_001`);
        if (!res.ok()) return "pending";
        const doc = (await res.json()) as { status: string };
        return doc.status;
      },
      { timeout: 30_000, intervals: [200, 500, 1000] },
    )
    .toBe("success");

  await page.goto(`/workspaces/${taskId}`);

  await expect(page.getByTestId("workspace-total-cost")).toHaveText(/^Total: \$\d+\.\d+$/);

  const breakdown = page.getByTestId("workspace-cost-breakdown");
  await expect(breakdown).toBeVisible();
  const rows = breakdown.locator('[data-testid^="run-summary-header-"]');
  const costs = breakdown.locator('[data-testid^="run-summary-cost-"]');
  await expect(rows.first()).toBeHidden();

  await page.getByText("Cost breakdown").click();
  await expect(rows.first()).toBeVisible();
  await expect(costs.first()).toHaveText(/^\$\d+\.\d+$/);
  await expect(breakdown.locator('[data-testid^="run-summary-output-tokens-"]')).toHaveCount(0);
  await expect(breakdown.locator('[data-testid^="run-summary-input-tokens-"]')).toHaveCount(0);
});
