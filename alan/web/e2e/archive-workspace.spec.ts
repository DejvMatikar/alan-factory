import { expect, test } from "./fixtures/test-base";

test("user can archive a workspace from the detail page without a dialog", { tag: "@no-claude" }, async ({
  page,
  request,
}) => {
  const title = `E2E Archive ${String(Date.now())}`;
  const res = await request.post("/api/workspaces", {
    data: { title, description: "archive me", budget: "medium" },
  });
  expect(res.status()).toBe(201);
  const { task_id: taskId } = (await res.json()) as { task_id: string };

  await page.goto(`/workspaces/${taskId}`);
  await expect(
    page.getByRole("heading", { level: 1, name: title }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Archive" }).click();

  await expect(page.getByTestId("delete-dialog")).toHaveCount(0);
  await expect(page.getByTestId("status-archived")).toBeVisible();

  const detail = await request.get(`/api/workspaces/${taskId}`);
  const ws = (await detail.json()) as { status: string };
  expect(ws.status).toBe("archived");
});
