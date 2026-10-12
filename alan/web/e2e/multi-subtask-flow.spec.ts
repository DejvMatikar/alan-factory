import { expect, test } from "./fixtures/test-base";

test(
  "multi-subtask workspace drives both subtasks to completion automatically",
  async ({ request }) => {
    test.setTimeout(90_000);
    const wsRes = await request.post("/api/workspaces", {
      data: {
        title: `e2e-multi-subtask-${String(Date.now())}`,
        description: "regression guard: both subtasks must reach completed",
        tasks: [
          { id: "task-a", team: "maintenance" },
          { id: "task-b", team: "maintenance" },
        ],
        dependencies: [{ from_id: "task-a", to_id: "task-b" }],
      },
    });
    expect(wsRes.ok()).toBeTruthy();
    const { task_id: wsId } = (await wsRes.json()) as { task_id: string };

    const dispRes = await request.post(
      `/api/workspaces/${wsId}/tasks/task-a/dispatch`,
      { data: { team: "maintenance" } },
    );
    expect(dispRes.ok()).toBeTruthy();

    await expect
      .poll(
        async () => {
          const res = await request.get(`/api/workspaces/${wsId}`);
          if (!res.ok()) return "pending";
          const doc = (await res.json()) as { status: string };
          return doc.status;
        },
        { timeout: 60_000, intervals: [500, 1000, 2000] },
      )
      .toBe("completed");

    const detail = (await (await request.get(`/api/workspaces/${wsId}`)).json()) as {
      tasks: { id: string; status: string }[];
    };
    expect(detail.tasks).toHaveLength(2);
    const taskA = detail.tasks.find((t) => t.id === "task-a");
    const taskB = detail.tasks.find((t) => t.id === "task-b");
    expect(taskA?.status).toBe("completed");
    expect(taskB?.status).toBe("completed");
  },
);
