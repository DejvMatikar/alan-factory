import type { APIRequestContext, Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import {
  registerGuardEngine,
  seedBlockingQuestion,
  seedProjectEngineKey,
  seedProjectFeatures,
  seedProjectIntendedEngineKey,
} from "../fixtures/seed";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf-8").trim();
}

const HOME_ENGINES = [
  { owner: "acme-platform", repo: "checkout-service" },
  { owner: "acme-platform", repo: "notification-worker" },
  { owner: "acme-platform", repo: "billing-reconciliation-service" },
];

async function findWorkspaceIdByTitle(request: APIRequestContext, title: string): Promise<string | null> {
  const res = await request.get("/api/workspaces");
  if (!res.ok()) return null;
  const rows = (await res.json()) as readonly { task_id: string; title: string }[];
  return rows.find((row) => row.title === title)?.task_id ?? null;
}

async function createWorkspace(
  request: APIRequestContext,
  title: string,
  description: string,
  engineKey?: string,
): Promise<string> {
  const existing = await findWorkspaceIdByTitle(request, title);
  if (existing !== null) return existing;
  const res = await request.post("/api/workspaces", {
    data: engineKey ? { title, description, engine_key: engineKey } : { title, description },
  });
  if (!res.ok()) {
    throw new Error(`desktop-audit max seed: failed to create workspace "${title}" (${String(res.status())})`);
  }
  const { task_id: taskId } = (await res.json()) as { task_id: string };
  return taskId;
}

async function findProjectIdByTitle(request: APIRequestContext, title: string): Promise<string | null> {
  const res = await request.get("/api/projects");
  if (!res.ok()) return null;
  const rows = (await res.json()) as readonly { id: string; title: string }[];
  return rows.find((row) => row.title === title)?.id ?? null;
}

async function createProject(
  request: APIRequestContext,
  title: string,
  intendedEngineKey?: string,
): Promise<string> {
  const existing = await findProjectIdByTitle(request, title);
  if (existing !== null) return existing;
  const res = await request.post("/api/projects", {
    data: intendedEngineKey ? { title, intended_engine_key: intendedEngineKey } : { title },
  });
  if (!res.ok()) {
    throw new Error(`desktop-audit max seed: failed to create project "${title}" (${String(res.status())})`);
  }
  const { id } = (await res.json()) as { id: string };
  return id;
}

async function seedHomeMax(request: APIRequestContext, root: string): Promise<void> {
  for (const engine of HOME_ENGINES) {
    await registerGuardEngine(request, root, engine.owner, engine.repo);
  }
  await createWorkspace(
    request,
    "Reconcile duplicate billing line items across the nightly settlement batch",
    "Long-running investigation into duplicate charges surfaced by the finance team's weekly export.",
    `${HOME_ENGINES[0]!.owner}/${HOME_ENGINES[0]!.repo}`,
  );
  await createWorkspace(
    request,
    "Add retry budget to the outbound notification worker",
    "Notifications silently drop under sustained provider 5xx bursts; add bounded retry with backoff.",
    `${HOME_ENGINES[1]!.owner}/${HOME_ENGINES[1]!.repo}`,
  );
  await createWorkspace(
    request,
    "Backfill missing settlement records for Q2",
    "",
    `${HOME_ENGINES[2]!.owner}/${HOME_ENGINES[2]!.repo}`,
  );
}

async function seedProjectsMax(request: APIRequestContext, root: string): Promise<void> {
  const boundTitle = "Checkout resilience program — retry, backoff, and settlement reconciliation";
  const boundId = await createProject(request, boundTitle);
  seedProjectEngineKey(root, boundId, `${HOME_ENGINES[0]!.owner}/${HOME_ENGINES[0]!.repo}`);

  const unboundTitle = "Notification worker hardening";
  const unboundEngineKey = `${HOME_ENGINES[1]!.owner}/${HOME_ENGINES[1]!.repo}`;
  const unboundId = await createProject(request, unboundTitle, unboundEngineKey);
  seedProjectIntendedEngineKey(root, unboundId, unboundEngineKey);
}

export const CONNECT_WIZARD_FIXTURE_OWNER = "e2e-mobile-audit-connect-co";
export const CONNECT_WIZARD_FIXTURE_REPO = "step3-fixture";

export async function seedConnectWizardFixtureEngine(
  request: APIRequestContext,
  root: string,
  authoritativeChecks: readonly string[],
): Promise<void> {
  const headers = { "X-Alan-Control-Token": readControlToken(root) };
  const slug = `${CONNECT_WIZARD_FIXTURE_OWNER}/${CONNECT_WIZARD_FIXTURE_REPO}`;
  const secretRes = await request.post("/api/secrets", {
    data: { engine: slug, purpose: "transport", value: "e2e-desktop-audit-placeholder-token" },
    headers,
  });
  if (!secretRes.ok() && secretRes.status() !== 409) {
    throw new Error(`desktop-audit seed: failed to seed connect-wizard secret (${String(secretRes.status())})`);
  }
  const res = await request.post("/api/engines", {
    data: {
      repo: { owner: CONNECT_WIZARD_FIXTURE_OWNER, repo: CONNECT_WIZARD_FIXTURE_REPO },
      forge: {
        repo: { owner: CONNECT_WIZARD_FIXTURE_OWNER, repo: CONNECT_WIZARD_FIXTURE_REPO },
        credential_env_name: "E2E_DESKTOP_AUDIT_CONNECT_GH_TOKEN",
        authoritative_checks: authoritativeChecks,
      },
      preset: "python",
    },
    headers,
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(`desktop-audit seed: failed to register connect-wizard engine (${String(res.status())})`);
  }
  const deleteRes = await request.delete("/api/secrets", { data: { engine: slug, purpose: "transport" }, headers });
  if (!deleteRes.ok() && deleteRes.status() !== 404) {
    throw new Error(`desktop-audit seed: failed to clean up connect-wizard secret (${String(deleteRes.status())})`);
  }
}

const CONNECT_WIZARD_MAX_CHECKS = ["lint", "typecheck", "test", "build"];

const EXPLORE_MAX_ENGINES = [
  { owner: "acme-platform", repo: "payments-gateway-adapter" },
  { owner: "acme-platform", repo: "fraud-signal-pipeline" },
];

async function seedExploreArchitectureMax(request: APIRequestContext, root: string): Promise<void> {
  const headers = { "X-Alan-Control-Token": readControlToken(root) };
  for (const engine of EXPLORE_MAX_ENGINES) {
    const res = await request.post("/api/engines", { data: { repo: engine }, headers });
    if (!res.ok() && res.status() !== 409) {
      throw new Error(
        `desktop-audit max seed: failed to register explore-architecture engine ${engine.owner}/${engine.repo} (${String(res.status())})`,
      );
    }
  }
}

async function seedWorkspaceDecisionMax(request: APIRequestContext, root: string): Promise<string> {
  const taskId = await createWorkspace(
    request,
    "Choose the durable storage backend for the settlement reconciliation ledger",
    "This decision blocks every downstream reconciliation workspace until it is resolved.",
  );
  seedBlockingQuestion(
    root,
    taskId,
    "Which storage backend should the settlement reconciliation ledger use for its append-only event log?",
    {
      status: "escalated",
      parkReason: null,
      proposedAnswerSpace: [
        "duckdb (embedded, zero ops)",
        "postgres (managed, needs a migration)",
        "sqlite with WAL (simplest, weakest concurrency)",
        "a dedicated event-log service (highest ops cost)",
      ],
      recommended: "duckdb (embedded, zero ops)",
      questionClass: "orchestrated_completeness",
    },
  );
  return taskId;
}

export interface DesktopMaxSeedResult {
  readonly routeParams: Readonly<Record<string, string>>;
}

export async function seedDesktopMaxState(
  page: Page,
  request: APIRequestContext,
  root: string,
): Promise<DesktopMaxSeedResult> {
  await seedProjectFeatures(page, true);
  await seedHomeMax(request, root);
  await seedProjectsMax(request, root);
  await seedConnectWizardFixtureEngine(request, root, CONNECT_WIZARD_MAX_CHECKS);
  await seedExploreArchitectureMax(request, root);
  const decisionTaskId = await seedWorkspaceDecisionMax(request, root);
  return {
    routeParams: {
      taskId: decisionTaskId,
    },
  };
}
