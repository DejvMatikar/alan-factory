import { expect, test } from "./fixtures/test-base";

test("dispatching from completed flips the workspace back to active", async ({
  request,
}) => {
  const create = await request.post("/api/workspaces", {
    data: {
      title: `e2e-redispatch-${Date.now()}`,
      description: "completed workspaces re-activate when dispatched",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const seed = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: { team: "maintenance" },
  });
  expect(seed.ok()).toBeTruthy();

  await expect
    .poll(async () => {
      const res = await request.get(`/api/workspaces/${taskId}`);
      if (!res.ok()) return "pending";
      const ws = (await res.json()) as { status: string };
      return ws.status;
    }, { timeout: 30_000, intervals: [200, 500, 1000] })
    .toBe("completed");

  const redispatch = await request.post(
    `/api/workspaces/${taskId}/dispatch`,
    { data: { team: "research" } },
  );
  expect(redispatch.status()).toBe(202);

  const detail = await request.get(`/api/workspaces/${taskId}`);
  const ws = (await detail.json()) as { status: string };
  expect(ws.status).toBe("active");

  const invList = await request.get(
    `/api/workspaces/${taskId}/invocations`,
  );
  const invs = (await invList.json()) as string[];
  expect(invs).toContain("inv_002");
});
