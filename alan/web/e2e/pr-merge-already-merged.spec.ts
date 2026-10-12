import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";

function ghMergeMarkerSize(root: string): number {
  try {
    return fs.statSync(path.join(root, "gh-merge-marker.log")).size;
  } catch {
    /* v8 ignore next */
    return 0;
  }
}

function writeApproveHandover(root: string, taskId: string, prNumber: number): void {
  const handoverDir = path.join(
    root,
    "workspaces",
    taskId,
    "tasks",
    taskId,
    "handovers",
  );
  fs.mkdirSync(handoverDir, { recursive: true });
  const timestamp = new Date().toISOString();
  const yaml = [
    `schema_version: "1.6"`,
    `handover_id: handover_${taskId}_aaaaaaa1`,
    `handover_type: forward`,
    `task_id: ${taskId}`,
    `from_team: verification`,
    `from_invocation_id: inv_001`,
    `to_team_suggestions:`,
    `- pr_merge`,
    `iteration: 2`,
    `revisit_of: null`,
    `timestamp_utc: ${timestamp.replace("Z", "+00:00")}`,
    `next_hop: null`,
    `decisions:`,
    `- id: verif-approve`,
    `  statement: "judge approved PR #${prNumber}"`,
    `  rationale: "verdict APPROVE"`,
    `  confidence: 1.0`,
    `  reversibility: low`,
    `open_questions: []`,
    `assumptions: []`,
    `constraints_inherited: []`,
    `artifacts_produced: []`,
    `full_synthesis: "VERDICT: APPROVE"`,
    `forbidden_actions: []`,
    `prior_attempts_summary: null`,
    `pr_number: ${prNumber}`,
    `files_to_edit: []`,
    `test_scenarios: []`,
    ``,
  ].join("\n");
  fs.writeFileSync(path.join(handoverDir, `handover_${taskId}_001.yaml`), yaml);
}

test.describe("pr_merge state machine", () => {
  test(
    "MERGED short-circuits to terminal without calling gh pr merge",
    { tag: "@no-claude" },
    async ({ request, e2eRoot }) => {
      const create = await request.post("/api/workspaces", {
        data: {
          title: `e2e-pr-merge-${Date.now()}`,
          description: "playwright drove pr_merge already_merged path",
          budget: "medium",
        },
      });
      expect(create.ok()).toBeTruthy();
      const { task_id: taskId } = (await create.json()) as { task_id: string };

      writeApproveHandover(e2eRoot, taskId, 42);

      const markerBaseline = ghMergeMarkerSize(e2eRoot);

      const dispatch = await request.post(
        `/api/workspaces/${taskId}/dispatch`,
        { data: { team: "pr_merge" } },
      );
      expect(dispatch.ok()).toBeTruthy();
      const { invocation_id: invId } = (await dispatch.json()) as {
        invocation_id: string;
      };

      await expect
        .poll(
          async () => {
            const res = await request.get(
              `/api/workspaces/${taskId}/invocations/${invId}`,
            );
            if (!res.ok()) return "pending";
            const doc = (await res.json()) as { status: string };
            return doc.status;
          },
          { timeout: 20_000, intervals: [200, 500, 1000] },
        )
        .toBe("success");

      expect(ghMergeMarkerSize(e2eRoot)).toBe(markerBaseline);

      const ws = await request.get(`/api/workspaces/${taskId}`);
      expect(ws.ok()).toBeTruthy();
    },
  );
});
