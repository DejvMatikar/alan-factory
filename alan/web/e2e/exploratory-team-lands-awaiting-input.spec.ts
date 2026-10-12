import { expect, test } from "./fixtures/test-base";

test("an exploratory team leaves the workspace in awaiting_input with no auto-route", async ({
  page,
  request,
}) => {
  const create = await request.post("/api/workspaces", {
    data: {
      title: `e2e-explore-${Date.now()}`,
      description: "exploratory teams pause for the human",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(
    `/api/workspaces/${taskId}/dispatch`,
    { data: { team: "research" } },
  );
  expect(dispatch.ok()).toBeTruthy();

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
      { timeout: 30_000, intervals: [200, 500, 1000] },
    )
    .toBe("success");

  await expect
    .poll(async () => {
      const res = await request.get(`/api/workspaces/${taskId}`);
      if (!res.ok()) return "pending";
      const ws = (await res.json()) as { status: string };
      return ws.status;
    }, { timeout: 10_000, intervals: [200, 500] })
    .toBe("awaiting_input");

  const invList = await request.get(
    `/api/workspaces/${taskId}/invocations`,
  );
  expect(invList.ok()).toBeTruthy();
  const invs = (await invList.json()) as string[];
  expect(invs).toEqual(["inv_001"]);

  await page.goto(`/workspaces/${taskId}`);
  await expect(page.getByTestId("status-awaiting_input")).toBeVisible();
});
