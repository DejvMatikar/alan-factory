import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { registerGuardEngine, seedProjectEngineKey } from "./fixtures/seed";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

async function registerEngine(
  request: APIRequestContext,
  e2eRoot: string,
  owner: string,
  repo: string,
  opts: { withForge?: boolean } = {},
): Promise<void> {
  const token = readControlToken(e2eRoot);
  let payload: Record<string, unknown> = { repo: { owner, repo } };
  if (opts.withForge) {
    const secretRes = await request.post("/api/secrets", {
      data: {
        engine: `${owner}/${repo}`,
        purpose: "transport",
        value: "ghp_e2e_toggle_placeholder",
      },
      headers: { "X-Alan-Control-Token": token },
    });
    expect(secretRes.status()).toBe(201);
    payload = {
      ...payload,
      forge: {
        repo: { owner, repo },
        credential_env_name: "E2E_TOGGLE_GH_TOKEN",
        authoritative_checks: [],
      },
    };
  }
  const res = await request.post("/api/engines", {
    data: payload,
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-toggle-blank-slate-guard-co");
});

async function createProject(request: APIRequestContext, title: string): Promise<string> {
  const res = await request.post("/api/projects", { data: { title } });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { id: string };
  return body.id;
}

async function createWorkspace(
  request: APIRequestContext,
  title: string,
  opts: Record<string, unknown> = {},
): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "project-features-toggle e2e", ...opts },
  });
  expect(res.ok()).toBeTruthy();
  const { task_id: taskId } = (await res.json()) as { task_id: string };
  return taskId;
}

function seedWorkspaceCost(e2eRoot: string, taskId: string, costUsd: number): void {
  const dir = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "invocations", "inv_001");
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

test(
  "home: no start-project action, no shipped/archived footer, and a project-bound workspace stays in a flat list whose spend reconciles",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());
    const owner = "e2e-toggle-off-co";
    const repo = `widget-${stamp}`;
    const engineKey = `${owner}/${repo}`;

    await registerEngine(request, e2eRoot, owner, repo, { withForge: true });
    const projectId = await createProject(request, `E2E Toggle Off Project ${stamp}`);
    seedProjectEngineKey(e2eRoot, projectId, engineKey);
    const wsId = await createWorkspace(request, `e2e-toggle-off-ws-${stamp}`, {
      project_id: projectId,
      engine_key: engineKey,
    });
    seedWorkspaceCost(e2eRoot, wsId, 3.5);
    await expect
      .poll(async () => {
        const res = await request.get(`/api/projects/${projectId}`);
        if (!res.ok()) return null;
        return ((await res.json()) as { engine_key: string | null }).engine_key;
      })
      .toBe(engineKey);

    await page.goto(`/repos/${engineKey}`);

    const section = page.getByTestId(`repo-section-${engineKey}`);
    await expect(section).toBeVisible();
    await expect(section.getByTestId(`home-row-feature-${wsId}`)).toBeVisible();
    await expect(section.getByTestId(`home-row-project-${projectId}`)).toHaveCount(0);
    await expect(section.getByTestId(`home-row-unassigned-${engineKey}`)).toHaveCount(0);
    await expect(page.getByTestId(`repo-start-project-${engineKey}`)).toHaveCount(0);
    await expect(page.getByTestId(`repo-footer-${engineKey}`)).toHaveCount(0);

    await expect(section.getByTestId(`aggregate-chip-${engineKey}-cost`)).toContainText("$3.50", {
      timeout: 10_000,
    });
  },
);

test(
  "home: with the toggle off, no unplaced section and no project row render anywhere, even for a provenance-less unbound project",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    await createProject(request, `E2E Toggle Off Unplaced ${stamp}`);

    await page.goto("/");

    await expect(page.getByTestId("repo-section-unplaced")).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 2, name: "No repository yet" })).toHaveCount(0);
    await expect(page.locator('[data-testid^="home-row-project-"]')).toHaveCount(0);
  },
);

test(
  "a repository with genuinely no workspaces still reads as an honest empty state, not broken",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());
    const owner = "e2e-toggle-off-empty-co";
    const repo = `widget-${stamp}`;
    const engineKey = `${owner}/${repo}`;
    await registerEngine(request, e2eRoot, owner, repo);

    await page.goto(`/repos/${engineKey}`);
    await expect(page.getByTestId(`repo-empty-${engineKey}`)).toHaveText("No active workspaces yet.");
  },
);

for (const routeSuffix of ["", "/some-id", "/some-id/engine"]) {
  test(
    `typed URL /projects${routeSuffix} redirects to home while the toggle is off`,
    { tag: "@no-claude" },
    async ({ page }) => {
      await page.goto(`/projects${routeSuffix}`);
      await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan");
      await expect(page.getByRole("heading", { level: 1, name: "Alan" })).toBeVisible();
    },
  );
}

test(
  "browser back from a page reached after a project deep link lands on home, not the project screen",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/");
    await page.goto("/projects/does-not-matter");
    await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan");
    await page.goto("/health");
    await page.goBack();
    await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan");
  },
);

function projectFixture(id: string, title: string) {
  return {
    id,
    title,
    status: "active",
    doc_ref: null,
    autonomy: "propose",
    budget_usd: null,
    engine_key: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    workspace_count: 0,
    total_spend_usd: 0,
  };
}

test(
  "Build chat with more than one project seeded creates a session immediately, with no chooser and an enabled composer",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.route("**/api/projects", async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        json: [projectFixture("proj-alpha", "Alpha Project"), projectFixture("proj-beta", "Beta Project")],
      });
    });

    await page.route("**/api/engines", async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({ json: { default_engine_key: "DejvMatikar/alan", engines: [] } });
    });

    await page.goto("/build");
    await expect(page.getByTestId("repo-chooser-modal")).toHaveCount(0);
    await expect(page).toHaveURL(/[?&]s=/, { timeout: 10_000 });
    await expect(page.getByLabel("Message")).toBeEnabled({ timeout: 10_000 });
  },
);

test(
  "a vision/scope amendment proposed while the toggle is off is still durably persisted server-side, even though no UI ever renders it",
  { tag: "@no-claude" },
  async ({ request }) => {
    const stamp = String(Date.now());
    const create = await request.post("/api/projects", {
      data: { title: `E2E Toggle Off Amendment ${stamp}` },
    });
    expect(create.ok()).toBeTruthy();
    const project = (await create.json()) as { id: string };

    const amend = await request.post(`/api/projects/${project.id}/amendments`, {
      data: { vision: "a new vision", scope: "a new scope", decision_entry: "e2e toggle-off amendment" },
    });
    expect(amend.ok()).toBeTruthy();
    const { fingerprint } = (await amend.json()) as { fingerprint: string };

    const approve = await request.post(
      `/api/projects/${project.id}/amendments/${fingerprint}/approve`,
    );
    expect(approve.status()).toBe(200);

    const secondAmend = await request.post(`/api/projects/${project.id}/amendments`, {
      data: {
        vision: "a second vision",
        scope: "a second scope",
        decision_entry: "e2e toggle-off amendment 2",
      },
    });
    expect(secondAmend.ok()).toBeTruthy();
  },
);

test(
  "toggling on from the top-bar Projects button takes effect immediately and survives a reload; a fresh browser context still defaults to off",
  { tag: "@no-claude" },
  async ({ page, browser }) => {
    await page.goto("/");
    const toggle = page.getByTestId("project-features-toggle");
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await expect(page.getByTestId("repo-start-project-DejvMatikar/alan")).toHaveCount(0);

    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("repo-start-project-DejvMatikar/alan")).toBeVisible();

    await page.reload();
    await expect(page.getByTestId("project-features-toggle")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByTestId("repo-start-project-DejvMatikar/alan")).toBeVisible();

    const freshContext = await browser.newContext();
    try {
      const freshPage = await freshContext.newPage();
      await freshPage.goto("/");
      await expect(freshPage.getByTestId("project-features-toggle")).toHaveAttribute(
        "aria-pressed",
        "false",
      );
      await expect(freshPage.getByTestId("repo-start-project-DejvMatikar/alan")).toHaveCount(0);
    } finally {
      await freshContext.close();
    }
  },
);
