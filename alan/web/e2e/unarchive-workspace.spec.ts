import { expect, test } from "./fixtures/test-base";

test("a workspace archived while active comes back needing the operator", { tag: "@no-claude" }, async ({
  page,
  request,
}) => {
  const title = `E2E Unarchive ${String(Date.now())}`;
  const res = await request.post("/api/workspaces", {
    data: { title, description: "unarchive me", budget: "medium" },
  });
  expect(res.status()).toBe(201);
  const { task_id: taskId } = (await res.json()) as { task_id: string };

  const archiveRes = await request.post(`/api/workspaces/${taskId}/archive`);
  expect(archiveRes.status()).toBe(204);

  await page.goto(`/workspaces/${taskId}`);
  await expect(page.getByTestId("status-archived")).toBeVisible();

  await page.getByRole("button", { name: "Unarchive" }).click();

  await expect(page.getByTestId("status-awaiting_input")).toBeVisible();
  await expect(page.getByTestId("park-reason-label")).toHaveText("Restored from archive");
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);

  const detail = await request.get(`/api/workspaces/${taskId}`);
  const ws = (await detail.json()) as { status: string };
  expect(ws.status).toBe("awaiting_input");
});
