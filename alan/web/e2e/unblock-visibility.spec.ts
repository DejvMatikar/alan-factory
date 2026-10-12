import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

function sqlEscape(value: string): string {
  return value.replace(/'/g, "''");
}

const UNBLOCK_LEDGER_DDL = `CREATE TABLE IF NOT EXISTS unblock_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  engine_key TEXT,
  workspace_key TEXT NOT NULL,
  scope_ref TEXT NOT NULL,
  shape TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  kind TEXT NOT NULL,
  action TEXT,
  outcome TEXT,
  head_sha TEXT,
  handover_id TEXT,
  bar_lowering INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT '',
  ref_id INTEGER
);`;

interface SeedRow {
  taskId: string;
  kind: "attempt" | "wait" | "report";
  outcome: string;
  action?: string | null;
  ts?: number;
  detail?: Record<string, unknown>;
}

function seedUnblockLedgerRow(e2eRoot: string, row: SeedRow): void {
  const dbPath = path.join(e2eRoot, "queue.db");
  const ts = row.ts ?? Date.now() / 1000;
  const action = row.action ?? null;
  const detailJson = sqlEscape(JSON.stringify(row.detail ?? {}));
  const insert = `INSERT INTO unblock_ledger
    (ts, engine_key, workspace_key, scope_ref, shape, fingerprint, kind, action, outcome,
     head_sha, handover_id, bar_lowering, cost_usd, detail, ref_id)
    VALUES (${ts}, NULL, '${sqlEscape(row.taskId)}', '${sqlEscape(row.taskId)}', 'stale_review',
     'stale_review-${sqlEscape(row.taskId)}', '${row.kind}',
     ${action === null ? "NULL" : `'${sqlEscape(action)}'`},
     '${sqlEscape(row.outcome)}', NULL, NULL, 0, 0, '${detailJson}', NULL);`;
  const script = `PRAGMA busy_timeout=5000;\n${UNBLOCK_LEDGER_DDL}\n${insert}`;
  execFileSync("sqlite3", [dbPath, script]);
}

async function createParkedWorkspace(
  request: APIRequestContext,
  title: string,
): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "unblock-visibility e2e" },
  });
  expect(res.ok()).toBeTruthy();
  const { task_id: taskId } = (await res.json()) as { task_id: string };
  return taskId;
}

function setWorkspaceStatus(e2eRoot: string, taskId: string, status: string): void {
  const metaPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "meta.json");
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
  meta.status = status;
  fs.writeFileSync(metaPath, JSON.stringify(meta));
}

test(
  "workspace page shows the host-waiting unblock notice, naming the 90-minute silence threshold",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const taskId = await createParkedWorkspace(request, `e2e-unblock-waiting-${Date.now()}`);
    setWorkspaceStatus(e2eRoot, taskId, "awaiting_input");
    seedUnblockLedgerRow(e2eRoot, {
      taskId,
      kind: "wait",
      outcome: "not_yet",
      detail: { reason: "readiness pending" },
    });

    await page.goto(`/workspaces/${taskId}`);

    const notice = page.getByTestId("unblock-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toHaveAttribute("aria-label", "Autonomous unblocker status");
    await expect(page.getByTestId("unblock-state-label")).toHaveText(
      "Host is waiting and re-probing",
    );
    await expect(page.getByTestId("unblock-updated")).toContainText(
      "90 minutes of silence",
    );
    await expect(page.getByTestId("unblock-authority-holder")).toHaveCount(0);
  },
);

test(
  "workspace page shows the needs-you unblock notice naming the authority holder and issue link",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const taskId = await createParkedWorkspace(request, `e2e-unblock-needs-human-${Date.now()}`);
    setWorkspaceStatus(e2eRoot, taskId, "escalated");
    seedUnblockLedgerRow(e2eRoot, {
      taskId,
      kind: "attempt",
      outcome: "acted",
      action: "dispatch:pr_merge",
    });
    seedUnblockLedgerRow(e2eRoot, {
      taskId,
      kind: "report",
      outcome: "needs_human",
      ts: Date.now() / 1000 + 1,
      detail: {
        reason: "per-workspace attempt ceiling reached",
        authority_holder: "human_operator",
      },
    });

    await page.goto(`/workspaces/${taskId}`);

    const notice = page.getByTestId("unblock-notice");
    await expect(notice).toBeVisible();
    await expect(page.getByTestId("unblock-state-label")).toHaveText("Needs you");
    await expect(page.getByTestId("unblock-authority-holder")).toContainText(
      "you, the human operator",
    );
    await expect(page.getByTestId("unblock-attempts")).toContainText("Attempt 1 of 5");
  },
);

test(
  "the workspace list marks a host-owned park distinctly, and shows nothing extra for a plain park",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    await registerGuardEngine(request, e2eRoot, "e2e-unblock-visibility-co", "unblock-spare");
    const stamp = Date.now();
    const hostOwnedId = await createParkedWorkspace(request, `e2e-unblock-list-host-${stamp}`);
    setWorkspaceStatus(e2eRoot, hostOwnedId, "awaiting_input");
    seedUnblockLedgerRow(e2eRoot, { taskId: hostOwnedId, kind: "wait", outcome: "not_yet" });

    const plainId = await createParkedWorkspace(request, `e2e-unblock-list-plain-${stamp}`);
    setWorkspaceStatus(e2eRoot, plainId, "awaiting_input");

    await page.goto("/");

    await expect(page.getByTestId(`unblock-owner-badge-${hostOwnedId}`)).toBeVisible();
    await expect(page.getByTestId(`unblock-owner-badge-${plainId}`)).toHaveCount(0);
  },
);
