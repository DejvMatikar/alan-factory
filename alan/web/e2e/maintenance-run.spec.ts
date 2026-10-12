import type { APIRequestContext } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { waitForGraphNode } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

const DEFAULT_REPO_KEY = "DejvMatikar/alan";
const MAINTENANCE_RUN_FIXTURE_OWNER = "e2e-maintenance-run-co";
const MAINTENANCE_RUN_FIXTURE_REPO = "widget";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

async function ensureNonDefaultEngine(request: APIRequestContext, root: string): Promise<void> {
  const res = await request.post("/api/engines", {
    data: { repo: { owner: MAINTENANCE_RUN_FIXTURE_OWNER, repo: MAINTENANCE_RUN_FIXTURE_REPO } },
    headers: { "X-Alan-Control-Token": readControlToken(root) },
  });
  if (res.ok() || res.status() === 409) return;
  throw new Error(`maintenance-run.spec: failed to seed non-default engine (${String(res.status())})`);
}

async function ensureAutonomousPosture(request: APIRequestContext, root: string): Promise<void> {
  const res = await request.post(`/api/engines/${DEFAULT_REPO_KEY}/posture`, {
    data: { posture: "autonomous", confirm: true },
    headers: { "X-Alan-Control-Token": readControlToken(root) },
  });
  if (!res.ok()) {
    throw new Error(`maintenance-run.spec: failed to set autonomous posture (${String(res.status())})`);
  }
}

test(
  "a maintenance run triggered from the per-repo Maintenance control composes every role into one leading verdict, including a visible unverified gap",
  async ({ page, request, e2eRoot }) => {
    test.setTimeout(210_000);

    await ensureNonDefaultEngine(request, e2eRoot);
    await ensureAutonomousPosture(request, e2eRoot);
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const maintenance = page.getByTestId(`repo-action-maintenance-${DEFAULT_REPO_KEY}`);
    await expect(maintenance).toBeVisible();
    await expect(maintenance).toHaveJSProperty("tagName", "BUTTON");
    await maintenance.click();

    const notice = page.getByTestId(`repo-maintenance-notice-${DEFAULT_REPO_KEY}`);
    await expect(notice).toBeVisible({ timeout: 20_000 });
    const href = await notice.getByRole("link").getAttribute("href");
    const taskId = decodeURIComponent((href ?? "").split("/workspaces/").at(1) ?? "");
    expect(taskId).not.toBe("");

    await expect
      .poll(
        async () => {
          const res = await request.get(
            `/api/workspaces/${taskId}/invocations/inv_002`,
          );
          if (!res.ok()) return "pending";
          const doc = (await res.json()) as { status: string };
          return doc.status;
        },
        { timeout: 60_000, intervals: [200, 500, 1000] },
      )
      .toBe("success");

    const wsRes = await request.get(`/api/workspaces/${taskId}`);
    expect(wsRes.ok()).toBeTruthy();
    const ws = (await wsRes.json()) as {
      latest_handover: { handover_id: string; full_synthesis: string } | null;
    };
    const handover = ws.latest_handover;
    if (handover === null) {
      throw new Error("expected a latest_handover after the maintenance run completed");
    }
    const synthesis = handover.full_synthesis;

    const verdictIndex = synthesis.indexOf("## Run verdict");
    expect(verdictIndex).toBe(0);
    expect(synthesis).toMatch(/RUN_VERDICT_SEVERITY: (NONE|LOW|MEDIUM|HIGH|CRITICAL)/);
    expect(synthesis).toContain(
      "Gaps (unverified — excluded from the severity roll-up):",
    );
    expect(synthesis).toContain("- ci_dependency_auditor:");

    const roles = [
      "code_health_analyst",
      "ci_dependency_auditor",
      "log_repo_scanner",
      "security_scanner",
    ];
    for (const role of roles) {
      const roleIndex = synthesis.indexOf(`## ${role}`);
      expect(roleIndex).toBeGreaterThan(verdictIndex);
    }
    expect(synthesis).toContain(
      "UNVERIFIED: gh is not in this role's tool allowlist in this environment",
    );

    await page.goto(`/workspaces/${taskId}`);
    const handoverNode = await waitForGraphNode(
      page,
      `handover-node-${handover.handover_id}`,
    );
    await handoverNode.click();
    await expect(page.getByTestId("handover-panel")).toBeVisible();
    await page.getByTestId("full-synthesis-disclosure").locator("summary").click();

    const fullSynthesis = page.getByTestId("full-synthesis");
    await expect(fullSynthesis.getByRole("heading", { level: 5 }).first()).toHaveText(
      "Run verdict",
    );
    await expect(fullSynthesis).toContainText("Gaps (unverified");
    await expect(fullSynthesis).toContainText("ci_dependency_auditor");
  },
);
