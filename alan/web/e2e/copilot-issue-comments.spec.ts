import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";

function taskDir(root: string, taskId: string): string {
  return path.join(root, "workspaces", taskId, "tasks", taskId);
}

function writeApproveHandover(root: string, taskId: string, prNumber: number): void {
  const handoverDir = path.join(taskDir(root, taskId), "handovers");
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

function listHandovers(root: string, taskId: string): string[] {
  const dir = path.join(taskDir(root, taskId), "handovers");
  try {
    return fs.readdirSync(dir).sort();
  } catch {
    /* v8 ignore next */
    return [];
  }
}

function listArtifacts(root: string, taskId: string): string[] {
  const dir = path.join(taskDir(root, taskId), ".alan");
  try {
    return fs.readdirSync(dir);
  } catch {
    /* v8 ignore next */
    return [];
  }
}

test.describe("copilot issue-comment shadow mode", () => {
  test.afterEach(({ e2eRoot }) => {
    fs.rmSync(path.join(e2eRoot, "gh-copilot-comments"), { force: true });
    fs.rmSync(path.join(e2eRoot, "gh-pr-state.json"), { force: true });
  });

  test(
    "pr_merge ignores a lone Copilot issue comment and merges normally",
    { tag: "@no-claude" },
    async ({ request, e2eRoot }) => {
      const create = await request.post("/api/workspaces", {
        data: {
          title: `e2e-copilot-comments-${Date.now()}`,
          description: "playwright tests copilot issue-comment shadow mode",
          budget: "medium",
        },
      });
      expect(create.ok()).toBeTruthy();
      const { task_id: taskId } = (await create.json()) as {
        task_id: string;
      };

      writeApproveHandover(e2eRoot, taskId, 42);

      fs.writeFileSync(path.join(e2eRoot, "gh-copilot-comments"), "1");
      fs.writeFileSync(path.join(e2eRoot, "gh-pr-state.json"), JSON.stringify(["OPEN"]));

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

      const handovers = listHandovers(e2eRoot, taskId);
      const allContents = handovers
        .map((h) =>
          fs.readFileSync(
            path.join(taskDir(e2eRoot, taskId), "handovers", h),
            "utf-8",
          ),
        )
        .join("\n---\n");
      expect(allContents).not.toContain("implementation_fix");

      const artifacts = listArtifacts(e2eRoot, taskId).filter((f) =>
        f.startsWith("copilot_threads_"),
      );
      expect(artifacts).toHaveLength(0);
    },
  );
});
