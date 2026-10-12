import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";
import { withDefaultEngineTransportCredential } from "./fixtures/transport-credential";

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
    `  rationale: "drives verification → impl_fix iteration"`,
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

function listHandovers(root: string, taskId: string): string[] {
  const dir = path.join(taskDir(root, taskId), "handovers");
  try {
    return fs.readdirSync(dir).sort();
  } catch {
    /* v8 ignore next */
    return [];
  }
}

function readHandover(root: string, taskId: string, filename: string): string {
  return fs.readFileSync(
    path.join(taskDir(root, taskId), "handovers", filename),
    "utf-8",
  );
}

withDefaultEngineTransportCredential();

test.describe("pr_iteration happy path", () => {
  test.afterEach(({ e2eRoot }) => {
    fs.rmSync(path.join(e2eRoot, "gh-pr-state.json"), { force: true });
    fs.rmSync(path.join(e2eRoot, "gh-checks-state"), { force: true });
  });

  test(
    "verification → implementation_fix → verification → pr_merge → completed",
    async ({ request, e2eRoot }) => {
      test.setTimeout(180_000);
      fs.writeFileSync(path.join(e2eRoot, "gh-pr-state.json"), JSON.stringify(["OPEN"]));
      fs.writeFileSync(path.join(e2eRoot, "gh-checks-state"), "SUCCESS");

      const create = await request.post("/api/workspaces", {
        data: {
          title: `e2e-pr-iter-${Date.now()}`,
          description: "playwright drove the full pr-iteration cascade",
          budget: "medium",
        },
      });
      expect(create.ok()).toBeTruthy();
      const { task_id: taskId } = (await create.json()) as { task_id: string };

      writeImplHandover(e2eRoot, taskId, 43);

      const dispatch = await request.post(
        `/api/workspaces/${taskId}/dispatch`,
        { data: { team: "verification" } },
      );
      expect(dispatch.ok()).toBeTruthy();

      await expect
        .poll(
          async () => {
            const res = await request.get(`/api/workspaces/${taskId}`);
            if (!res.ok()) return "pending";
            const doc = (await res.json()) as { status: string };
            return doc.status;
          },
          { timeout: 90_000, intervals: [500, 1000, 2000] },
        )
        .toBe("completed");

      const handovers = listHandovers(e2eRoot, taskId);
      expect(handovers.length).toBeGreaterThanOrEqual(5);

      const bodies = handovers.map((f) => readHandover(e2eRoot, taskId, f));
      const fromTeamRe = /^from_team:\s*(\S+)/m;
      const fromTeams = bodies.map((b) => {
        const match = fromTeamRe.exec(b);
        return match ? match[1] : "?";
      });

      expect(fromTeams).toContain("implementation_fix");
      expect(fromTeams).toContain("pr_merge");
      expect(fromTeams).not.toContain("retrospective");
      const verifCount = fromTeams.filter((t) => t === "verification").length;
      expect(verifCount).toBeGreaterThanOrEqual(2);

      const prMergeBodies = bodies.filter((b) => /^from_team:\s*pr_merge/m.test(b));
      expect(prMergeBodies.length).toBeGreaterThanOrEqual(1);
      const dryRunBody = prMergeBodies.find((b) =>
        b.includes("PR #43 would have been merged (dry run)"),
      );
      const shortCircuitBody = prMergeBodies.find((b) =>
        b.includes("PR #43 already merged (idempotency short-circuit)"),
      );
      expect(dryRunBody ?? shortCircuitBody).toBeDefined();
      expect(prMergeBodies.every((b) => /handover_type:\s*terminal/.test(b))).toBe(
        true,
      );
    },
  );
});
