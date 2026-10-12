import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import {
  registerGuardEngine,
  seedProjectEngineKey,
  seedProjectFeatures,
  seedProjectIntendedEngineKey,
} from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const SECOND_REPO_OWNER = "e2e-home-table-co";
const SECOND_REPO_NAME = "widgets";
const SECOND_REPO_KEY = `${SECOND_REPO_OWNER}/${SECOND_REPO_NAME}`;
const DEFAULT_REPO_KEY = "DejvMatikar/alan";
const THIRD_REPO_OWNER = "e2e-home-table-sized";
const THIRD_REPO_NAME = "delivery";
const THIRD_REPO_KEY = `${THIRD_REPO_OWNER}/${THIRD_REPO_NAME}`;

test.beforeEach(async ({ page, request, e2eRoot }) => {
  await seedProjectFeatures(page, true);
  await registerGuardEngine(request, e2eRoot, SECOND_REPO_OWNER, SECOND_REPO_NAME);
});

const ROADMAP_MARKDOWN = `# Roadmap

## First item

Do the first thing.

## Second item

Do the second thing.
`;

async function createWorkspace(
  request: APIRequestContext,
  title: string,
  opts: Record<string, unknown> = {},
): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "home-repo-table e2e", ...opts },
  });
  expect(res.ok()).toBeTruthy();
  const { task_id: taskId } = (await res.json()) as { task_id: string };
  return taskId;
}

async function createProject(request: APIRequestContext, title: string): Promise<string> {
  const res = await request.post("/api/projects", { data: { title } });
  expect(res.ok()).toBeTruthy();
  const { id } = (await res.json()) as { id: string };
  return id;
}

async function setWorkspaceNote(request: APIRequestContext, taskId: string, note: string): Promise<void> {
  const res = await request.put(`/api/workspaces/${taskId}/note`, { data: { note } });
  expect(res.ok()).toBeTruthy();
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

function seedWorkspaceTeam(e2eRoot: string, taskId: string, team: string): void {
  const metaPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId, "meta.json");
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
  meta.team = team;
  fs.writeFileSync(metaPath, JSON.stringify(meta), "utf-8");
}

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

async function registerForgeEngine(
  request: APIRequestContext,
  e2eRoot: string,
  owner: string,
  repo: string,
): Promise<void> {
  const token = readControlToken(e2eRoot);
  const secretRes = await request.post("/api/secrets", {
    data: { engine: `${owner}/${repo}`, purpose: "transport", value: "ghp_e2e_home_table_placeholder" },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(secretRes.status()).toBe(201);
  const res = await request.post("/api/engines", {
    data: {
      repo: { owner, repo },
      forge: {
        repo: { owner, repo },
        credential_env_name: "E2E_HOME_TABLE_GH_TOKEN",
        authoritative_checks: [],
      },
    },
    headers: { "X-Alan-Control-Token": token },
  });
  if (res.status() !== 201 && res.status() !== 409) {
    throw new Error(`failed to register forge engine ${owner}/${repo} (${String(res.status())})`);
  }
}

function seedDeliveredWorkspace(
  e2eRoot: string,
  taskId: string,
  opts: { prNumber: number; filesToEdit: string[] },
): void {
  const wsPath = path.join(e2eRoot, "workspaces", taskId, "tasks", taskId);

  const handoversDir = path.join(wsPath, "handovers");
  fs.mkdirSync(handoversDir, { recursive: true });
  const filesYaml = opts.filesToEdit.map((f) => `  - "${f}"`).join("\n");
  const handoverYaml = [
    'schema_version: "1.20"',
    `handover_id: "handover_${taskId}_001"`,
    "handover_type: forward",
    `task_id: "${taskId}"`,
    "from_team: implementation_plan",
    "from_invocation_id: inv_001",
    "iteration: 1",
    'timestamp_utc: "2026-06-13T00:00:00+00:00"',
    "full_synthesis: stub plan synthesis",
    `pr_number: ${String(opts.prNumber)}`,
    "files_to_edit:",
    filesYaml,
  ].join("\n");
  fs.writeFileSync(path.join(handoversDir, `handover_${taskId}_001.yaml`), handoverYaml, "utf-8");

  const invDir = path.join(wsPath, "invocations", "inv_001");
  fs.mkdirSync(invDir, { recursive: true });
  const workflowYaml = [
    'schema_version: "1.7"',
    "id: inv_001",
    `namespace: "alan.workspace.${taskId}"`,
    "team: implementation",
    "status: success",
    'started_at: "2026-06-13T00:00:00+00:00"',
    'completed_at: "2026-06-13T00:10:00+00:00"',
    "tasks:",
    "  - id: t1",
    "    type: io.alan.LLMStep",
    "    role: implementer",
    "    model: claude-sonnet-5",
    "    status: success",
    '    started_at: "2026-06-13T00:00:00+00:00"',
    '    completed_at: "2026-06-13T00:10:00+00:00"',
  ].join("\n");
  fs.writeFileSync(path.join(invDir, "workflow.yaml"), workflowYaml, "utf-8");

  const metaPath = path.join(wsPath, "meta.json");
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8")) as Record<string, unknown>;
  meta.status = "archived";
  fs.writeFileSync(metaPath, JSON.stringify(meta), "utf-8");
}

test(
  "renders a repo-sectioned table with live data on each repository's own page, folding the default repo's two representations into one section",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());

    const secondRepoProjectId = await createProject(request, `E2E Second Repo Project ${stamp}`);
    seedProjectEngineKey(e2eRoot, secondRepoProjectId, SECOND_REPO_KEY);
    const secondRepoWsId = await createWorkspace(request, `e2e-second-repo-ws-${stamp}`, {
      project_id: secondRepoProjectId,
      engine_key: SECOND_REPO_KEY,
    });

    const defaultWsNullEngineId = await createWorkspace(request, `e2e-default-null-engine-${stamp}`);
    const defaultWsExplicitId = await createWorkspace(request, `e2e-default-explicit-engine-${stamp}`, {
      engine_key: DEFAULT_REPO_KEY,
    });

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const defaultSection = page.getByTestId(`repo-section-${DEFAULT_REPO_KEY}`);
    await expect(defaultSection).toHaveCount(1);
    await expect(defaultSection.getByRole("heading", { level: 1, name: "Alan" })).toHaveCount(1);
    await expect(defaultSection.locator("thead th").first()).toHaveText("Actions");
    await expect(defaultSection.getByTestId(`home-row-feature-${defaultWsNullEngineId}`)).toBeVisible();
    await expect(defaultSection.getByTestId(`home-row-feature-${defaultWsExplicitId}`)).toBeVisible();

    const defaultUnassigned = defaultSection.getByTestId(`home-row-unassigned-${DEFAULT_REPO_KEY}`);
    await expect(defaultUnassigned).toBeVisible();
    await expect(defaultUnassigned).toContainText("—");
    await expect(defaultUnassigned.getByRole("link")).toHaveCount(0);

    await page.goto(`/repos/${SECOND_REPO_KEY}`);
    const secondSection = page.getByTestId(`repo-section-${SECOND_REPO_KEY}`);
    await expect(secondSection.getByRole("heading", { level: 1, name: "Widgets" })).toBeVisible();
    const secondProjectRow = secondSection.getByTestId(`home-row-project-${secondRepoProjectId}`);
    await expect(secondProjectRow).toBeVisible();
    await expect(
      secondProjectRow.getByRole("link", { name: `E2E Second Repo Project ${stamp}` }),
    ).toHaveAttribute("href", `/projects/${secondRepoProjectId}`);
    await expect(secondSection.getByTestId(`home-row-feature-${secondRepoWsId}`)).toBeVisible();

    const secondRepoRow = secondSection.getByTestId(`home-row-feature-${secondRepoWsId}`);
    await expect(secondRepoRow.getByRole("button")).toHaveCount(1);
    await expect(secondRepoRow.getByTestId(`workspace-row-action-${secondRepoWsId}`)).toHaveAccessibleName(
      `Stop — e2e-second-repo-ws-${stamp}`,
    );
    await expect(page.getByRole("checkbox")).toHaveCount(0);
  },
);

test(
  "an unbound project with recorded intent renders under its intended repository with an Unbound chip; a provenance-less project is reachable from /projects instead, and the default repo's own unassigned bucket is unaffected",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());

    const intendedProjectId = await createProject(request, `E2E Intended Project ${stamp}`);
    seedProjectIntendedEngineKey(e2eRoot, intendedProjectId, SECOND_REPO_KEY);

    const unplacedProjectId = await createProject(request, `E2E Unplaced Project ${stamp}`);
    await createWorkspace(request, `e2e-default-unassigned-${stamp}`);

    await page.goto(`/repos/${SECOND_REPO_KEY}`);
    const secondSection = page.getByTestId(`repo-section-${SECOND_REPO_KEY}`);
    const intendedRow = secondSection.getByTestId(`home-row-project-${intendedProjectId}`);
    await expect(intendedRow).toBeVisible();
    await expect(intendedRow.getByTestId(`unbound-chip-${intendedProjectId}`)).toHaveText("Unbound");
    await expect(page.getByTestId("repo-section-unplaced")).toHaveCount(0);

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const defaultUnassigned = page.getByTestId(`home-row-unassigned-${DEFAULT_REPO_KEY}`);
    await expect(defaultUnassigned).toBeVisible();
    await expect(defaultUnassigned).toContainText("Unassigned");

    await page.goto("/projects");
    await expect(
      page.getByRole("link", { name: `E2E Unplaced Project ${stamp}` }),
    ).toHaveAttribute("href", `/projects/${unplacedProjectId}`);
  },
);

test(
  "degradations render honestly and no phase rail, meter, checkpoint, plan-approved or fingerprint markup ever appears",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());

    const noRoadmapProjectId = await createProject(request, `E2E No Roadmap ${stamp}`);
    seedProjectEngineKey(e2eRoot, noRoadmapProjectId, DEFAULT_REPO_KEY);
    const roadmapProjectId = await createProject(request, `E2E With Roadmap ${stamp}`);
    seedProjectEngineKey(e2eRoot, roadmapProjectId, DEFAULT_REPO_KEY);
    const importRes = await request.post(`/api/projects/${roadmapProjectId}/roadmap/import`, {
      data: { markdown: ROADMAP_MARKDOWN },
    });
    expect(importRes.ok()).toBeTruthy();

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);

    const noRoadmapRow = page.getByTestId(`home-row-project-${noRoadmapProjectId}`);
    await expect(noRoadmapRow).toContainText("planning · no plan yet");
    await expect(noRoadmapRow).toContainText("—");

    const roadmapRow = page.getByTestId(`home-row-project-${roadmapProjectId}`);
    await expect(roadmapRow).toContainText("roadmap · 0 of 2 items");
    await expect(roadmapRow).toContainText("0/2");

    const body = page.locator("body");
    await expect(body).not.toContainText(/plan approved/i);
    await expect(body).not.toContainText(/checkpoints? answered/i);
    await expect(body).not.toContainText(/fingerprint/i);
    await expect(page.locator('[data-testid*="phase-rail"]')).toHaveCount(0);
    await expect(page.locator('[data-testid*="phase-meter"]')).toHaveCount(0);
    await expect(page.locator('[data-testid*="checkpoint"]')).toHaveCount(0);
  },
);

test(
  "renders the Repository Status strip, honestly unknown — never red, never hidden — for a repo with no forge configured",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${SECOND_REPO_KEY}`);

    const strip = page.getByTestId(`repo-status-strip-${SECOND_REPO_KEY}`);
    await expect(strip).toBeVisible();
    await expect(strip.getByRole("heading", { level: 2, name: "Repository Status" })).toBeVisible();

    for (const suffix of ["main-ci", "open-prs", "cost", "last-deploy"]) {
      const chip = strip.getByTestId(`aggregate-chip-${SECOND_REPO_KEY}-${suffix}`);
      await expect(chip).toContainText("unknown");
      await expect(chip).not.toHaveClass(/status-danger/);
    }
    await expect(strip.getByTestId(`aggregate-chip-${SECOND_REPO_KEY}-health`)).toHaveCount(0);

    const section = page.getByTestId(`repo-section-${SECOND_REPO_KEY}`);
    for (const suffix of ["maintenance", "hide", "settings"]) {
      const control = section.getByTestId(`repo-action-${suffix}-${SECOND_REPO_KEY}`);
      const describedBy = await control.getAttribute("aria-describedby");
      expect(describedBy).not.toBeNull();
      const description = page.locator(`[id="${String(describedBy)}"]`);
      await expect(description).toHaveClass(/sr-only/);
      await control.focus();
      await expect(description).not.toHaveClass(/sr-only/);
    }

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const defaultStrip = page.getByTestId(`repo-status-strip-${DEFAULT_REPO_KEY}`);
    await expect(defaultStrip).toBeVisible();
    for (const label of ["Main CI", "Open PRs", "Cost", "Delivery time", "Last deploy"]) {
      await expect(defaultStrip.getByText(label, { exact: true })).toBeVisible();
    }
    await expect(defaultStrip.getByText("Health", { exact: true })).toHaveCount(0);
  },
);

test(
  "shows a real median-with-sample-count delivery chip for a delivered workspace, and an explicit no-data chip for an untouched size bucket",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    await registerForgeEngine(request, e2eRoot, THIRD_REPO_OWNER, THIRD_REPO_NAME);
    const taskId = await createWorkspace(request, `e2e-delivered-${String(Date.now())}`, {
      engine_key: THIRD_REPO_KEY,
    });
    seedDeliveredWorkspace(e2eRoot, taskId, { prNumber: 42, filesToEdit: ["a.py", "b.py"] });

    await page.goto(`/repos/${THIRD_REPO_KEY}`);
    const strip = page.getByTestId(`repo-status-strip-${THIRD_REPO_KEY}`);
    await expect(strip).toBeVisible();

    const xsChip = strip.getByTestId(`aggregate-chip-${THIRD_REPO_KEY}-delivery-XS`);
    await expect(xsChip).toContainText("XS · 10m · n=1");

    const mChip = strip.getByTestId(`aggregate-chip-${THIRD_REPO_KEY}-delivery-M`);
    await expect(mChip).toContainText("M · no data");
    await expect(mChip).not.toContainText("0s");
  },
);

test(
  "the repo section's shipped/archived footer links to the projects view",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/");
    const footer = page.getByTestId(`repo-footer-${DEFAULT_REPO_KEY}`);
    await expect(footer).toBeVisible();
    await footer.getByRole("link").click();
    await expect(page).toHaveURL(/\/projects$/);
    await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
  },
);

test(
  "no auto-archive badge ever renders on Home in this e2e environment, where automatic archival is switched off",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    await createWorkspace(request, `e2e-no-auto-archive-${stamp}`);
    await page.goto("/");
    await expect(page.locator('[data-testid^="auto-archive-badge-"]')).toHaveCount(0);
  },
);

test(
  "a workspace whose spend is a fraction of a cent shows a non-zero Cost cell",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-subcent-cost-${stamp}`);
    seedWorkspaceCost(e2eRoot, taskId, 0.0034);

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();
    await expect(row).toContainText("$0.0034", { timeout: 10_000 });
  },
);

test(
  "a feature row shows the personal note's first line instead of the title",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const title = `E2E Note Feature ${stamp}`;
    const taskId = await createWorkspace(request, title);
    await setWorkspaceNote(request, taskId, "the note that replaces the title");

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();
    await expect(row).toContainText(taskId);
    await expect(row).toContainText("the note that replaces the title");
    await expect(row).not.toContainText(title);
  },
);

test(
  "a feature row with no note shows the identifier alone",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-no-note-feature-${stamp}`);

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();
    const primaryCellText = (await row.locator("td").nth(1).innerText()).trim();
    expect(primaryCellText).toBe(taskId);
  },
);

test(
  "a feature row with a multi-line note shows only its first line",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-multiline-note-${stamp}`);
    await setWorkspaceNote(request, taskId, "line one visible\nline two hidden");

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toContainText("line one visible");
    await expect(row).not.toContainText("line two hidden");
  },
);

test(
  "the workspace link keeps id · title as its accessible name even though the note is shown instead",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const title = `e2e-a11y-note-feature-${stamp}`;
    const taskId = await createWorkspace(request, title);
    await setWorkspaceNote(request, taskId, "a11y note");

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row.getByRole("link", { name: `${taskId} · ${title}` })).toHaveAttribute(
      "href",
      `/workspaces/${taskId}`,
    );
  },
);

test(
  "the note span carries the complete first line in its title attribute for hover access",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-hover-note-${stamp}`);
    const longLine = `hover reveal line ${stamp} ${"x".repeat(180)}`;
    await setWorkspaceNote(request, taskId, longLine);

    await page.goto("/");
    const noteSpan = page.getByTestId(`ws-note-${taskId}`);
    await expect(noteSpan).toHaveAttribute("title", longLine);
  },
);

test(
  "a feature row with a long note stays single-line at a narrow viewport",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-narrow-note-${stamp}`);
    await setWorkspaceNote(request, taskId, `a fairly long note about this workspace's purpose ${stamp} ${"y".repeat(120)}`);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();
    const [scrollHeight, clientHeight] = await row.evaluate((el) => [el.scrollHeight, el.clientHeight]);
    expect(scrollHeight - clientHeight).toBeLessThan(8);
  },
);

test(
  "a feature row with both a note and a badge stacks the task id, note, and badge row top to bottom",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-stack-order-${stamp}`);
    await setWorkspaceNote(request, taskId, "stack order note");
    seedWorkspaceTeam(e2eRoot, taskId, "dependency_sweep");

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toBeVisible();
    const link = row.getByRole("link");
    const note = page.getByTestId(`ws-note-${taskId}`);
    const badge = page.getByTestId(`dep-badge-${taskId}`);
    await expect(link).toBeVisible();
    await expect(note).toBeVisible();
    await expect(badge).toBeVisible();

    const linkBox = await link.boundingBox();
    const noteBox = await note.boundingBox();
    const badgeBox = await badge.boundingBox();
    if (linkBox === null || noteBox === null || badgeBox === null) {
      throw new Error("expected the link, note, and badge to each have a bounding box");
    }
    expect(linkBox.y).toBeLessThan(noteBox.y);
    expect(noteBox.y).toBeLessThan(badgeBox.y);
  },
);

test(
  "an unassigned-group workspace shows its note the same as any other feature row",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const stamp = String(Date.now());
    const taskId = await createWorkspace(request, `e2e-unassigned-note-${stamp}`);
    await setWorkspaceNote(request, taskId, "unassigned group note");

    await page.goto("/");
    const row = page.getByTestId(`home-row-feature-${taskId}`);
    await expect(row).toContainText("unassigned group note");
  },
);
