import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";
import { withDefaultEngineTransportCredential } from "./fixtures/transport-credential";

interface DispatchInstructionsReadback {
  status: string;
  text: string | null;
  origin: string | null;
}

function taskDir(root: string, taskId: string): string {
  return path.join(root, "workspaces", taskId, "tasks", taskId);
}

function writeImplHandover(root: string, taskId: string, prNumber: number): void {
  const handoverDir = path.join(taskDir(root, taskId), "handovers");
  fs.mkdirSync(handoverDir, { recursive: true });
  const timestamp = new Date().toISOString();
  const yaml = [
    `schema_version: "1.6"`,
    `handover_id: handover_${taskId}_aaaaaaa1`,
    `handover_type: forward`,
    `task_id: ${taskId}`,
    `from_team: implementation`,
    `from_invocation_id: inv_001`,
    `to_team_suggestions:`,
    `- verification`,
    `iteration: 1`,
    `revisit_of: null`,
    `timestamp_utc: ${timestamp.replace("Z", "+00:00")}`,
    `next_hop: null`,
    `decisions:`,
    `- id: impl-d1`,
    `  statement: "stub implementation committed feature.py"`,
    `  rationale: "drives verification -> impl_fix iteration"`,
    `  confidence: 0.9`,
    `  reversibility: medium`,
    `open_questions: []`,
    `assumptions: []`,
    `constraints_inherited: []`,
    `artifacts_produced: []`,
    `full_synthesis: "stub impl ran and committed feature.py"`,
    `forbidden_actions: []`,
    `prior_attempts_summary: null`,
    `pr_number: ${prNumber}`,
    `files_to_edit: []`,
    `test_scenarios: []`,
    ``,
  ].join("\n");
  fs.writeFileSync(path.join(handoverDir, `handover_${taskId}_001.yaml`), yaml);
}

withDefaultEngineTransportCredential();

test("dispatching with instructions persists a sticky instruction, and Clear removes it via the backend", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const create = await request.post("/api/workspaces", {
    data: {
      title: `e2e-sticky-dispatch-${Date.now()}`,
      description: "sticky-instructions e2e",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: { team: "research", instructions: "focus on the widget module" },
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

  const stickyRes = await request.get(`/api/workspaces/${taskId}/sticky-instructions`);
  expect(stickyRes.ok()).toBeTruthy();
  expect(await stickyRes.json()).toEqual({
    text: "focus on the widget module",
    origin: "dispatch",
  });

  await page.goto(`/workspaces/${taskId}`);
  const card = page.getByTestId("sticky-instruction-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("focus on the widget module");
  await expect(page.getByTestId("sticky-instruction-origin")).toContainText(
    "typed for a prior dispatch",
  );

  await page.getByTestId("sticky-instruction-clear").click();

  await expect(page.getByTestId("sticky-instruction-cleared-notice")).toBeVisible();
  await expect(card).toHaveCount(0);
  await expect(page.getByTestId("sticky-instruction-cleared-notice")).toBeFocused();

  const afterClear = await request.get(`/api/workspaces/${taskId}/sticky-instructions`);
  expect(await afterClear.json()).toEqual({ text: null, origin: null });
});

test("an automatically-routed continuation inherits the sticky instruction without retyping", async ({
  request,
  e2eRoot,
}) => {
  test.setTimeout(120_000);
  fs.writeFileSync(path.join(e2eRoot, "gh-pr-state.json"), JSON.stringify(["OPEN"]));
  fs.writeFileSync(path.join(e2eRoot, "gh-checks-state"), "SUCCESS");
  try {
    const create = await request.post("/api/workspaces", {
      data: {
        title: `e2e-sticky-inherit-${Date.now()}`,
        description: "sticky-instructions inheritance e2e",
        budget: "medium",
      },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    writeImplHandover(e2eRoot, taskId, 77);

    const dispatch = await request.post(`/api/workspaces/${taskId}/dispatch`, {
      data: { team: "verification", instructions: "pay special attention to feature.py" },
    });
    expect(dispatch.ok()).toBeTruthy();

    await expect
      .poll(
        async () => {
          const res = await request.get(`/api/workspaces/${taskId}/invocations`);
          if (!res.ok()) return 0;
          const invs = (await res.json()) as string[];
          return invs.length;
        },
        { timeout: 90_000, intervals: [500, 1000, 2000] },
      )
      .toBeGreaterThanOrEqual(2);

    const readbackRes = await request.get(`/api/workspaces/${taskId}/dispatch-instructions`);
    expect(readbackRes.ok()).toBeTruthy();
    const readbacks = (await readbackRes.json()) as Record<string, DispatchInstructionsReadback>;
    const inherited = Object.values(readbacks).find((r) => r.origin === "inherited");
    expect(inherited).toBeDefined();
    expect(inherited?.text).toBe("pay special attention to feature.py");
  } finally {
    fs.rmSync(path.join(e2eRoot, "gh-pr-state.json"), { force: true });
    fs.rmSync(path.join(e2eRoot, "gh-checks-state"), { force: true });
  }
});

test("re-dispatching with new instructions replaces the persisted instruction rather than appending", async ({
  request,
}) => {
  test.setTimeout(90_000);
  const create = await request.post("/api/workspaces", {
    data: {
      title: `e2e-sticky-replace-${Date.now()}`,
      description: "sticky-instructions replace e2e",
      budget: "medium",
    },
  });
  expect(create.ok()).toBeTruthy();
  const { task_id: taskId } = (await create.json()) as { task_id: string };

  const first = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: { team: "research", instructions: "first observation" },
  });
  expect(first.ok()).toBeTruthy();

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

  const second = await request.post(`/api/workspaces/${taskId}/dispatch`, {
    data: { team: "research", instructions: "second observation" },
  });
  expect(second.ok()).toBeTruthy();

  const stickyRes = await request.get(`/api/workspaces/${taskId}/sticky-instructions`);
  expect(await stickyRes.json()).toEqual({ text: "second observation", origin: "dispatch" });
});
