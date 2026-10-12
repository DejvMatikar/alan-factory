import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedControlToken, seedProjectEngineKey, seedProjectFeatures } from "./fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

async function seedTransportSecret(
  request: APIRequestContext,
  token: string,
  slug: string,
): Promise<void> {
  const res = await request.post("/api/secrets", {
    data: { engine: slug, purpose: "transport", value: "ghp_e2e_ops_transport" },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

async function registerEngine(
  request: APIRequestContext,
  token: string,
  owner: string,
  repo: string,
): Promise<void> {
  const slug = `${owner}/${repo}`;
  await seedTransportSecret(request, token, slug);
  const res = await request.post("/api/engines", {
    data: {
      repo: { owner, repo },
      forge: {
        repo: { owner, repo },
        credential_env_name: "E2E_OPS_GH_TOKEN",
        authoritative_checks: [],
      },
    },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

async function createProject(request: APIRequestContext, title: string): Promise<string> {
  const res = await request.post("/api/projects", { data: { title } });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { id: string };
  return body.id;
}

async function waitForEngineKeySeeded(
  request: APIRequestContext,
  projectId: string,
  engineKey: string,
): Promise<void> {
  await expect
    .poll(async () => {
      const res = await request.get(`/api/projects/${projectId}`);
      if (!res.ok()) return null;
      const body = (await res.json()) as { engine_key: string | null };
      return body.engine_key;
    })
    .toBe(engineKey);
}

test.describe("project ops panel", () => {
  test(
    "surfaces identity, posture, pool, preflight and CI/deploy tiles for a project bound to a non-default engine",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-ops-co";
      const repo = `widget-${String(Date.now())}`;
      const engineKey = `${owner}/${repo}`;

      await registerEngine(request, token, owner, repo);
      const projectId = await createProject(request, `E2E Ops Panel ${String(Date.now())}`);
      seedProjectEngineKey(e2eRoot, projectId, engineKey);
      await waitForEngineKeySeeded(request, projectId, engineKey);

      await page.goto(`/projects/${projectId}`);

      const panel = page.getByTestId("engine-ops-panel");
      await expect(panel).toBeVisible();
      await expect(page.getByTestId("engine-ops-identity")).toHaveText(engineKey);
      await expect(page.getByTestId("engine-ops-posture-pill")).toHaveText("Observe");
      await expect(page.getByTestId("engine-ops-pool-error")).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId("engine-ops-preflight-never-run")).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId("engine-ops-ci-deploy-healthy")).toBeVisible({ timeout: 20_000 });
    },
  );

  test(
    "does not render an ops panel for a project on the default engine, and the rest of the detail view is unchanged",
    { tag: "@no-claude" },
    async ({ page, request }) => {
      const projectId = await createProject(
        request,
        `E2E Ops Panel Default ${String(Date.now())}`,
      );

      await page.goto(`/projects/${projectId}`);

      await expect(page.getByTestId("project-doc")).toBeVisible();
      await expect(page.getByTestId("engine-ops-panel")).toHaveCount(0);
    },
  );

  test(
    "posture is changeable only from the confirm modal, only after explicit confirmation, and the pill updates solely via the SSE re-read",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-ops-posture-co";
      const repo = `widget-${String(Date.now())}`;
      const engineKey = `${owner}/${repo}`;

      await registerEngine(request, token, owner, repo);
      const projectId = await createProject(request, `E2E Ops Posture ${String(Date.now())}`);
      seedProjectEngineKey(e2eRoot, projectId, engineKey);
      await waitForEngineKeySeeded(request, projectId, engineKey);

      await seedControlToken(page, e2eRoot);
      await page.goto(`/projects/${projectId}`);
      await expect(page.getByTestId("engine-ops-posture-pill")).toHaveText("Observe");

      await page.getByTestId("engine-ops-posture-change-button").click();
      const dialog = page.getByTestId("posture-confirm-dialog");
      await expect(dialog).toBeVisible();
      await page.getByRole("button", { name: "Cancel" }).click();
      await expect(dialog).toBeHidden();
      await expect(page.getByTestId("engine-ops-posture-pill")).toHaveText("Observe");

      await page.getByTestId("engine-ops-posture-change-button").click();
      await expect(dialog).toBeVisible();
      await dialog.getByTestId("posture-confirm-select").selectOption("propose");
      await dialog.getByTestId("posture-confirm-submit").click();
      await expect(dialog).toBeHidden({ timeout: 10_000 });

      await expect(page.getByTestId("engine-ops-posture-pill")).toHaveText("Propose", {
        timeout: 10_000,
      });

      const detailRes = await request.get(`/api/engines/${owner}/${repo}`);
      const detailBody = (await detailRes.json()) as { posture: string };
      expect(detailBody.posture).toBe("propose");
    },
  );

  test(
    "network cadence: tile requests stay flat while idle and connected, and one relevant change event triggers exactly one re-read per tile",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-ops-cadence-co";
      const repo = `widget-${String(Date.now())}`;

      await registerEngine(request, token, owner, repo);
      const projectId = await createProject(request, `E2E Ops Cadence ${String(Date.now())}`);
      seedProjectEngineKey(e2eRoot, projectId, `${owner}/${repo}`);
      await waitForEngineKeySeeded(request, projectId, `${owner}/${repo}`);

      const counts = { posture: 0, pool: 0, preflight: 0 };
      page.on("request", (req) => {
        if (req.method() !== "GET") return;
        const url = req.url();
        if (url.endsWith(`/api/engines/${owner}/${repo}`)) counts.posture += 1;
        else if (url.endsWith(`/api/engines/${owner}/${repo}/pool`)) counts.pool += 1;
        else if (url.endsWith(`/api/engines/${owner}/${repo}/preflight`)) counts.preflight += 1;
      });

      await page.goto(`/projects/${projectId}`);
      await expect(page.getByTestId("engine-ops-identity")).toHaveText(`${owner}/${repo}`);
      await expect(page.getByTestId("engine-ops-pool-error")).toBeVisible();
      await expect(page.getByTestId("engine-ops-preflight-never-run")).toBeVisible();

      await page.waitForTimeout(1500);
      const baseline = { ...counts };

      await page.waitForTimeout(3000);
      expect(counts).toEqual(baseline);

      const preflightRes = await request.post(`/api/engines/${owner}/${repo}/preflight`, {
        headers: { "X-Alan-Control-Token": token },
      });
      expect(preflightRes.ok()).toBeTruthy();

      await expect.poll(() => counts.posture, { timeout: 10_000 }).toBe(baseline.posture + 1);
      await expect.poll(() => counts.pool).toBe(baseline.pool + 1);
      await expect.poll(() => counts.preflight).toBe(baseline.preflight + 1);

      await page.waitForTimeout(1000);
      expect(counts.posture).toBe(baseline.posture + 1);
      expect(counts.pool).toBe(baseline.pool + 1);
      expect(counts.preflight).toBe(baseline.preflight + 1);
    },
  );
});

function invocationDir(e2eRoot: string, taskId: string, invId: string): string {
  return path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "invocations", invId);
}

function seedWorkspaceCost(e2eRoot: string, taskId: string, costUsd: number): void {
  const dir = invocationDir(e2eRoot, taskId, "inv_001");
  fs.mkdirSync(dir, { recursive: true });
  const workflowYaml = [
    'schema_version: "1.7"',
    "id: inv_001",
    `namespace: "alan.workspace.${taskId}"`,
    "team: implementation",
    "status: success",
    'started_at: "2026-06-13T00:00:00+00:00"',
    'completed_at: "2026-06-13T00:05:00+00:00"',
    "tasks:",
    "  - id: t1",
    "    type: io.alan.LLMStep",
    "    role: implementer",
    "    model: claude-sonnet-5",
    "    status: success",
    `    total_cost_usd: ${String(costUsd)}`,
  ].join("\n");
  fs.writeFileSync(path.join(dir, "workflow.yaml"), workflowYaml, "utf-8");
}

async function setProjectBudget(
  request: APIRequestContext,
  projectId: string,
  budgetUsd: number,
): Promise<void> {
  const res = await request.patch(`/api/projects/${projectId}`, {
    data: { budget_usd: budgetUsd },
  });
  expect(res.ok()).toBeTruthy();
}

async function createWorkspaceForProject(
  request: APIRequestContext,
  projectId: string,
  title: string,
): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "seed workspace for budget e2e", project_id: projectId },
  });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { task_id: string };
  return body.task_id;
}

test.describe("project budget panel", () => {
  test(
    "renders the normal state, with the execution-spend-only caption, when no budget is set",
    { tag: "@no-claude" },
    async ({ page, request }) => {
      const projectId = await createProject(request, `E2E Budget Normal ${String(Date.now())}`);

      await page.goto(`/projects/${projectId}`);

      const burn = page.getByTestId("project-burn");
      await expect(burn).toBeVisible();
      await expect(page.getByTestId("project-budget-pill")).toHaveText("Normal");
      await expect(burn).toContainText("Spend is within budget.");
      await expect(burn).toContainText(
        "Total spend across every category, including planning-chat and collision-check costs.",
      );
    },
  );

  test(
    "renders the approaching-limit state, with the amber pill, once spend crosses the warning threshold",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const stamp = String(Date.now());
      const projectId = await createProject(request, `E2E Budget Approaching ${stamp}`);
      const taskId = await createWorkspaceForProject(request, projectId, `Budget WS ${stamp}`);
      seedWorkspaceCost(e2eRoot, taskId, 85);
      await setProjectBudget(request, projectId, 100);

      await page.goto(`/projects/${projectId}`);

      const pill = page.getByTestId("project-budget-pill");
      await expect(pill).toHaveText("Approaching limit", { timeout: 10_000 });
      await expect(pill).toHaveClass(/text-btn-reset/);
      await expect(page.getByTestId("project-burn")).toContainText(
        "Spend is approaching this project's budget limit.",
      );
    },
  );

  test(
    "renders the paused state — stating in words that new work is paused and in-flight work will finish — once spend reaches the budget",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const stamp = String(Date.now());
      const projectId = await createProject(request, `E2E Budget Paused ${stamp}`);
      const taskId = await createWorkspaceForProject(request, projectId, `Budget WS ${stamp}`);
      seedWorkspaceCost(e2eRoot, taskId, 60);
      await setProjectBudget(request, projectId, 50);

      await page.goto(`/projects/${projectId}`);

      const pill = page.getByTestId("project-budget-pill");
      await expect(pill).toHaveText("Paused", { timeout: 10_000 });
      await expect(pill).toHaveClass(/text-btn-destroy/);
      const burn = page.getByTestId("project-burn");
      await expect(burn).toContainText("New work is paused");
      await expect(burn).toContainText("in flight will finish normally");
    },
  );

  test(
    "shows a visible unavailable state — never a hidden section — when the server can't return spend data",
    { tag: "@no-claude" },
    async ({ page, request }) => {
      const projectId = await createProject(
        request,
        `E2E Budget Unavailable ${String(Date.now())}`,
      );
      const detailRes = await request.get(`/api/projects/${projectId}`);
      expect(detailRes.ok()).toBeTruthy();
      const detail = (await detailRes.json()) as Record<string, unknown>;
      detail.budget = null;

      await page.route(`**/api/projects/${projectId}`, async (route) => {
        if (route.request().method() !== "GET") {
          await route.continue();
          return;
        }
        await route.fulfill({ status: 200, json: detail });
      });

      await page.goto(`/projects/${projectId}`);

      await expect(page.getByTestId("project-burn")).toBeVisible();
      await expect(page.getByTestId("project-burn-unavailable")).toHaveText(
        "Spend data unavailable.",
      );
    },
  );
});
