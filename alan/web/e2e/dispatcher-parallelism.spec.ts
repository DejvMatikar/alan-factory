import fs from "node:fs";
import path from "node:path";

import { waitForDispatcherIdle } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

interface WsSummary {
  task_id: string;
  latest_invocation_status: string | null;
}

test.beforeEach(async ({ request }) => {
  test.setTimeout(160_000);
  await waitForDispatcherIdle(request, { timeout: 60_000 });
});

test("two workspaces dispatched concurrently rendezvous mid-team-run", async ({
  request,
  e2eRoot,
}) => {
  const rendezvousFile = path.join(e2eRoot, "rendezvous.log");
  fs.mkdirSync(path.dirname(rendezvousFile), { recursive: true });
  fs.writeFileSync(rendezvousFile, "");

  try {
    const ts = String(Date.now());
    const [res1, res2] = await Promise.all([
      request.post("/api/workspaces", {
        data: {
          title: `e2e-parallel-1-${ts}`,
          description: "parallel dispatcher test 1",
          budget: "medium",
        },
      }),
      request.post("/api/workspaces", {
        data: {
          title: `e2e-parallel-2-${ts}`,
          description: "parallel dispatcher test 2",
          budget: "medium",
        },
      }),
    ]);
    expect(res1.ok()).toBeTruthy();
    expect(res2.ok()).toBeTruthy();
    const { task_id: taskId1 } = (await res1.json()) as { task_id: string };
    const { task_id: taskId2 } = (await res2.json()) as { task_id: string };

    const [disp1, disp2] = await Promise.all([
      request.post(`/api/workspaces/${taskId1}/dispatch`, {
        data: { team: "research" },
      }),
      request.post(`/api/workspaces/${taskId2}/dispatch`, {
        data: { team: "research" },
      }),
    ]);
    expect(disp1.ok()).toBeTruthy();
    expect(disp2.ok()).toBeTruthy();

    await expect
      .poll(
        async () => {
          const res = await request.get("/api/workspaces");
          const all = (await res.json()) as WsSummary[];
          const ws1 = all.find((w) => w.task_id === taskId1);
          const ws2 = all.find((w) => w.task_id === taskId2);
          return (
            ws1?.latest_invocation_status === "running" &&
            ws2?.latest_invocation_status === "running"
          );
        },
        { timeout: 20_000, intervals: [100, 200, 500] },
      )
      .toBe(true);

    for (const taskId of [taskId1, taskId2]) {
      await expect
        .poll(
          async () => {
            const res = await request.get(`/api/workspaces/${taskId}`);
            if (!res.ok()) return "pending";
            const ws = (await res.json()) as { status: string };
            return ws.status;
          },
          { timeout: 30_000, intervals: [200, 500, 1000] },
        )
        .toBe("awaiting_input");
    }

    const rvLines = fs
      .readFileSync(rendezvousFile, "utf8")
      .split("\n")
      .filter((l) => l.trim() !== "");
    expect(rvLines.length).toBeGreaterThanOrEqual(2);
  } finally {
    try {
      fs.unlinkSync(rendezvousFile);
    } catch {
      void 0;
    }
  }
});
