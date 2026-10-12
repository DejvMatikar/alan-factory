import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";
import { withDefaultEngineTransportCredential } from "./fixtures/transport-credential";

withDefaultEngineTransportCredential();

test.afterEach(({ e2eRoot }) => {
  fs.rmSync(path.join(e2eRoot, "gh-pr-state.json"), { force: true });
  fs.rmSync(path.join(e2eRoot, "gh-checks-state"), { force: true });
});

function taskDir(root: string, taskId: string): string {
  return path.join(root, "workspaces", taskId, "tasks", taskId);
}

function writeScenarioHandover(root: string, taskId: string): void {
  const handoverDir = path.join(taskDir(root, taskId), "handovers");
  fs.mkdirSync(handoverDir, { recursive: true });
  const timestamp = new Date().toISOString().replace("Z", "+00:00");
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
    `timestamp_utc: ${timestamp}`,
    `next_hop: null`,
    `decisions:`,
    `- id: impl-d1`,
    `  statement: "stub implementation committed"`,
    `  rationale: "drives verification scenario runner e2e"`,
    `  confidence: 0.9`,
    `  reversibility: medium`,
    `open_questions: []`,
    `assumptions: []`,
    `constraints_inherited: []`,
    `artifacts_produced: []`,
    `full_synthesis: "stub impl_fix ran (verification scenario_runner e2e)"`,
    `forbidden_actions: []`,
    `prior_attempts_summary: null`,
    `pr_number: 777`,
    `files_to_edit: []`,
    `test_scenarios:`,
    `- id: s_pass`,
    `  description: "pwd always succeeds"`,
    `  given: "workspace cwd exists"`,
    `  when: "pwd"`,
    `  then: ""`,
    `- id: s_fail`,
    `  description: "predicate mismatch fails"`,
    `  given: "workspace cwd exists"`,
    `  when: "pwd"`,
    `  then: "THIS_WILL_NOT_MATCH_8f3a9c"`,
    `- id: s_skipped`,
    `  description: "empty when is skipped by the runner"`,
    `  given: "no command to run"`,
    `  when: ""`,
    `  then: ""`,
    ``,
  ].join("\n");
  fs.writeFileSync(path.join(handoverDir, `handover_${taskId}_001.yaml`), yaml);
}

function writeCannotVerifyHandover(root: string, taskId: string): void {
  const handoverDir = path.join(taskDir(root, taskId), "handovers");
  fs.mkdirSync(handoverDir, { recursive: true });
  const timestamp = new Date().toISOString().replace("Z", "+00:00");
  const yaml = [
    `schema_version: "1.6"`,
    `handover_id: handover_${taskId}_cannot1`,
    `handover_type: forward`,
    `task_id: ${taskId}`,
    `from_team: implementation`,
    `from_invocation_id: inv_001`,
    `to_team_suggestions:`,
    `- verification`,
    `iteration: 1`,
    `revisit_of: null`,
    `timestamp_utc: ${timestamp}`,
    `next_hop: null`,
    `decisions:`,
    `- id: impl-d1`,
    `  statement: "stub implementation committed"`,
    `  rationale: "drives cannot_verify e2e path"`,
    `  confidence: 0.9`,
    `  reversibility: medium`,
    `open_questions: []`,
    `assumptions: []`,
    `constraints_inherited: []`,
    `artifacts_produced: []`,
    `full_synthesis: "stub for cannot_verify e2e"`,
    `forbidden_actions: []`,
    `prior_attempts_summary: null`,
    `pr_number: 999999999`,
    `files_to_edit: []`,
    `test_scenarios: []`,
    ``,
  ].join("\n");
  fs.writeFileSync(path.join(handoverDir, `handover_${taskId}_001.yaml`), yaml);
}

test(
  "verification cannot_verify: trigger file bypasses pipeline and workspace reaches awaiting_input",
  async ({ request, e2eRoot }) => {
    const triggerPath = path.join(e2eRoot, "cannot-verify-trigger");
    try {
      const create = await request.post("/api/workspaces", {
        data: {
          title: `e2e-cannot-verify-${Date.now()}`,
          description: "playwright tests cannot_verify path via trigger file",
          budget: "medium",
        },
      });
      expect(create.ok()).toBeTruthy();
      const { task_id: taskId } = (await create.json()) as { task_id: string };

      fs.writeFileSync(triggerPath, taskId);

      writeCannotVerifyHandover(e2eRoot, taskId);

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
          { timeout: 45_000, intervals: [500, 1000, 2000] },
        )
        .toBe("awaiting_input");

      const invDir = path.join(taskDir(e2eRoot, taskId), "invocations");
      let foundScenarioRunnerStream = false;
      for (const inv of fs.readdirSync(invDir).sort()) {
        const p = path.join(
          invDir,
          inv,
          "agents",
          "scenario_runner.stream.jsonl",
        );
        if (fs.existsSync(p)) {
          foundScenarioRunnerStream = true;
          break;
        }
      }
      expect(foundScenarioRunnerStream).toBe(false);
    } finally {
      if (fs.existsSync(triggerPath)) {
        fs.unlinkSync(triggerPath);
      }
    }
  },
);

test(
  "verification scenario_runner executes scenarios and workspace reaches completed",
  async ({ request, e2eRoot }) => {
    const create = await request.post("/api/workspaces", {
      data: {
        title: `e2e-scenario-runner-${Date.now()}`,
        description: "playwright drives scenario_runner subprocess execution",
        budget: "medium",
      },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    writeScenarioHandover(e2eRoot, taskId);

    fs.writeFileSync(path.join(e2eRoot, "gh-pr-state.json"), JSON.stringify(["OPEN"]));
    fs.writeFileSync(path.join(e2eRoot, "gh-checks-state"), "SUCCESS");

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
        { timeout: 45_000, intervals: [500, 1000, 2000] },
      )
      .toBe("completed");

    const handoverDir = path.join(taskDir(e2eRoot, taskId), "handovers");
    const handovers = fs.readdirSync(handoverDir).sort();
    expect(handovers.length).toBeGreaterThanOrEqual(2);

    const bodies = handovers.map((f) =>
      fs.readFileSync(path.join(handoverDir, f), "utf-8"),
    );
    const fromTeams = bodies.map((b) => {
      const m = /^from_team:\s*(\S+)/m.exec(b);
      return m ? m[1] : "?";
    });
    expect(fromTeams).toContain("verification");

    const invDir = path.join(taskDir(e2eRoot, taskId), "invocations");
    let streamContent = "";
    for (const inv of fs.readdirSync(invDir).sort()) {
      const p = path.join(invDir, inv, "agents", "scenario_runner.stream.jsonl");
      if (fs.existsSync(p)) {
        streamContent = fs.readFileSync(p, "utf-8");
        break;
      }
    }
    expect(streamContent).toContain("PASS");
    expect(streamContent).toContain("FAIL");
  },
);
