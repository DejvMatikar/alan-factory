import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext, Page, Route } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedControlToken } from "./fixtures/seed";

interface EngineConfigBody {
  repo: { owner: string; repo: string };
  code_quality: unknown;
  forge: unknown;
  ci: unknown;
  roles: Record<string, unknown>;
  gate_commands: string[][];
  warmup_commands: string[][];
  base_tools: string[];
  team_tools: Record<string, string[]>;
  transition_graph: Record<string, string[]>;
  flow_policy: unknown;
  posture: string;
  pool_size: number | null;
  preset: string | null;
  prompt_overrides: Record<string, unknown>;
}

const PROPOSABLE_FIELD_ROWS = [
  "gate-commands",
  "warmup-commands",
  "base-tools",
  "team-tools",
  "roles",
  "transition-graph",
  "flow-policy",
];

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

function uniqueRepo(prefix: string): { owner: string; repo: string } {
  return { owner: `${prefix}-co`, repo: `${prefix}-${String(Date.now())}` };
}

async function seedTransportSecret(
  request: APIRequestContext,
  token: string,
  slug: string,
): Promise<void> {
  const res = await request.post("/api/secrets", {
    data: { engine: slug, purpose: "transport", value: "ghp_e2e_step2_placeholder" },
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
  await seedTransportSecret(request, token, `${owner}/${repo}`);
  const res = await request.post("/api/engines", {
    data: {
      repo: { owner, repo },
      forge: {
        repo: { owner, repo },
        credential_env_name: "E2E_STEP2_GH_TOKEN",
        authoritative_checks: [],
      },
      preset: "python",
    },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

async function getEngineConfig(
  request: APIRequestContext,
  owner: string,
  repo: string,
): Promise<EngineConfigBody> {
  const res = await request.get(`/api/engines/${owner}/${repo}`);
  expect(res.ok()).toBeTruthy();
  return res.json() as Promise<EngineConfigBody>;
}

async function raisePosture(
  request: APIRequestContext,
  token: string,
  owner: string,
  repo: string,
  posture: string,
): Promise<void> {
  const res = await request.post(`/api/engines/${owner}/${repo}/posture`, {
    data: { posture, confirm: true },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.ok()).toBeTruthy();
}

function proposedFrom(baseline: EngineConfigBody): EngineConfigBody {
  return { ...baseline, gate_commands: [["uv", "run", "pytest", "-q"]], base_tools: [...baseline.base_tools, "Grep"] };
}

interface ProposalResultOverrides {
  base_preset?: string;
}

function makeProposalResult(
  engineKey: string,
  baseline: EngineConfigBody,
  overrides: ProposalResultOverrides = {},
) {
  const proposed = proposedFrom(baseline);
  return {
    slug: engineKey,
    base_preset: overrides.base_preset ?? "python",
    detected_stacks: ["python"],
    additional_stacks: ["node"],
    proposed_config: proposed,
    diff: [
      { field: "gate_commands", baseline: baseline.gate_commands, proposed: proposed.gate_commands, changed: true },
      { field: "warmup_commands", baseline: baseline.warmup_commands, proposed: baseline.warmup_commands, changed: false },
      { field: "base_tools", baseline: baseline.base_tools, proposed: proposed.base_tools, changed: true },
      { field: "team_tools", baseline: baseline.team_tools, proposed: baseline.team_tools, changed: false },
      { field: "roles", baseline: baseline.roles, proposed: baseline.roles, changed: false },
      { field: "transition_graph", baseline: baseline.transition_graph, proposed: baseline.transition_graph, changed: false },
      { field: "flow_policy", baseline: baseline.flow_policy, proposed: baseline.flow_policy, changed: false },
    ],
    validation: { check: "flow_graph_and_posture_valid", status: "pass", message: "ok", remedy: "", remedy_url: "" },
  };
}

async function stubProposal(
  page: Page,
  owner: string,
  repo: string,
  handler: (route: Route) => Promise<void> | void,
): Promise<void> {
  await page.route(`**/api/engines/${owner}/${repo}/config-proposal`, handler);
}

async function stubValidate(page: Page, owner: string, repo: string): Promise<void> {
  await page.route(`**/api/engines/${owner}/${repo}/config-draft-validate`, async (route: Route) => {
    await route.fulfill({
      json: { check: "flow_graph_and_posture_valid", status: "pass", message: "ok", remedy: "", remedy_url: "" },
    });
  });
}

async function setUpRegisteredEngine(
  request: APIRequestContext,
  token: string,
  prefix: string,
): Promise<{ owner: string; repo: string; engineKey: string; baseline: EngineConfigBody }> {
  const { owner, repo } = uniqueRepo(prefix);
  await registerEngine(request, token, owner, repo);
  const baseline = await getEngineConfig(request, owner, repo);
  return { owner, repo, engineKey: `${owner}/${repo}`, baseline };
}

test.describe("connect wizard — step 2 (configure engine)", () => {
  test(
    "renders exactly the seven proposable rows plus code_quality/pool_size as baseline fields, and posture nowhere",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo, engineKey, baseline } = await setUpRegisteredEngine(request, token, "e2e-step2-rows");
      await seedControlToken(page, e2eRoot);
      await stubProposal(page, owner, repo, async (route) => {
        await route.fulfill({ json: makeProposalResult(engineKey, baseline) });
      });
      await stubValidate(page, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/configure`);
      await expect(page.getByTestId("connect-step2-configure")).toBeVisible();
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      for (const field of PROPOSABLE_FIELD_ROWS) {
        await expect(page.getByTestId(`connect-step2-diff-${field}`)).toBeVisible();
      }
      await expect(page.getByTestId("step2-baseline-fields")).toBeVisible();
      await expect(page.getByTestId("step2-pool-size")).toBeVisible();
      const stepContent = await page.getByTestId("connect-step2-configure").innerText();
      expect(stepContent.toLowerCase()).not.toContain("posture");
    },
  );

  test(
    "a delayed proposal renders the named in-flight state, never a bare spinner, before settling",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo, engineKey, baseline } = await setUpRegisteredEngine(request, token, "e2e-step2-delay");
      await seedControlToken(page, e2eRoot);
      await stubProposal(page, owner, repo, async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 800));
        await route.fulfill({ json: makeProposalResult(engineKey, baseline) });
      });
      await stubValidate(page, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/configure`);
      await expect(page.getByTestId("step2-phase")).toContainText(/drafting a config proposal/i);
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("step2-phase")).toContainText(/proposal ready/i);
    },
  );

  test(
    "editing a proposed field re-validates, debounced, with the draft-only chip copy",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo, engineKey, baseline } = await setUpRegisteredEngine(request, token, "e2e-step2-validate");
      await seedControlToken(page, e2eRoot);
      await stubProposal(page, owner, repo, async (route) => {
        await route.fulfill({ json: makeProposalResult(engineKey, baseline) });
      });
      let validateCalls = 0;
      await page.route(`**/api/engines/${owner}/${repo}/config-draft-validate`, async (route: Route) => {
        validateCalls += 1;
        await route.fulfill({
          json: { check: "flow_graph_and_posture_valid", status: "pass", message: "ok", remedy: "", remedy_url: "" },
        });
      });

      await page.goto(`/connect/${owner}/${repo}/configure`);
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("step2-validation-caption")).toHaveText(
        "validated as a draft — nothing saved yet",
      );
      const initialCalls = validateCalls;
      const baseToolsEditor = page.getByTestId("connect-step2-diff-base-tools-now-editor");
      await baseToolsEditor.fill(`${baseline.base_tools.join("\n")}\nEdit`);
      await expect(() => {
        expect(validateCalls).toBeGreaterThan(initialCalls);
      }).toPass({ timeout: 15_000 });
    },
  );

  test(
    "two distinct documented 422 causes render two distinct messages — never the same wording",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const aborted = await setUpRegisteredEngine(request, token, "e2e-step2-aborted");
      await seedControlToken(page, e2eRoot);
      await stubProposal(page, aborted.owner, aborted.repo, async (route) => {
        await route.fulfill({
          status: 422,
          json: {
            detail: {
              check: "config_proposal",
              status: "fail",
              message: `a config-proposal pipeline is already running for '${aborted.engineKey}'`,
              remedy: "seed the engine configuration from a stack preset manually in the engine configuration editor",
              remedy_url: "",
            },
          },
        });
      });
      await page.goto(`/connect/${aborted.owner}/${aborted.repo}/configure`);
      const abortedFinding = await page.getByTestId("step2-fallback-message").textContent();
      await expect(page.getByTestId("engine-preset-seed-open")).toBeVisible();

      const unrepairable = await setUpRegisteredEngine(request, token, "e2e-step2-unrepairable");
      await stubProposal(page, unrepairable.owner, unrepairable.repo, async (route) => {
        await route.fulfill({
          status: 422,
          json: {
            detail: {
              check: "config_proposal",
              status: "fail",
              message: "model output does not contain a parseable JSON object",
              remedy: "seed the engine configuration from a stack preset manually in the engine configuration editor",
              remedy_url: "",
            },
          },
        });
      });
      await page.goto(`/connect/${unrepairable.owner}/${unrepairable.repo}/configure`);
      const unrepairableFinding = await page.getByTestId("step2-fallback-message").textContent();

      expect(abortedFinding).not.toBeNull();
      expect(unrepairableFinding).not.toBeNull();
      expect(abortedFinding).not.toBe(unrepairableFinding);
      expect(abortedFinding).toContain("already running");
      expect(unrepairableFinding).toContain("parseable JSON");
    },
  );

  test(
    "choosing a different base preset re-runs the proposal against it",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo, engineKey, baseline } = await setUpRegisteredEngine(request, token, "e2e-step2-preset");
      await seedControlToken(page, e2eRoot);
      let lastBody: { base_preset: string | null } | undefined;
      await stubProposal(page, owner, repo, async (route) => {
        lastBody = route.request().postDataJSON() as { base_preset: string | null };
        const preset = lastBody.base_preset ?? "python";
        await route.fulfill({ json: makeProposalResult(engineKey, baseline, { base_preset: preset }) });
      });
      await stubValidate(page, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/configure`);
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      await page.getByTestId("step2-preset-node").click();
      await expect(() => {
        expect(lastBody?.base_preset).toBe("node");
      }).toPass({ timeout: 5_000 });
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
    },
  );

  test(
    "save carries the engine's already-raised posture through unchanged, even after stepping back from step 4",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo, engineKey, baseline } = await setUpRegisteredEngine(request, token, "e2e-step2-posture");
      await raisePosture(request, token, owner, repo, "propose");
      await seedControlToken(page, e2eRoot);
      await stubProposal(page, owner, repo, async (route) => {
        await route.fulfill({ json: makeProposalResult(engineKey, baseline) });
      });
      await stubValidate(page, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/configure`);
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      await page.getByTestId("step2-footer-primary").click();
      await expect(page).toHaveURL(new RegExp(`/connect/${owner}/${repo}/dispatchable$`), { timeout: 15_000 });

      const saved = await getEngineConfig(request, owner, repo);
      expect(saved.posture).toBe("propose");
    },
  );

  test(
    "save writes nothing to the target repo and navigates to the dispatchable step",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo, engineKey, baseline } = await setUpRegisteredEngine(request, token, "e2e-step2-save");
      await seedControlToken(page, e2eRoot);
      await stubProposal(page, owner, repo, async (route) => {
        await route.fulfill({ json: makeProposalResult(engineKey, baseline) });
      });
      await stubValidate(page, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/configure`);
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      await page.getByTestId("step2-footer-primary").click();
      await expect(page).toHaveURL(new RegExp(`/connect/${owner}/${repo}/dispatchable$`), { timeout: 15_000 });

      const onboardingRes = await request.get(`/api/engines/${owner}/${repo}/onboarding`);
      expect(onboardingRes.ok()).toBeTruthy();
      const onboarding = (await onboardingRes.json()) as { state: { scaffold_pr_number: number | null } };
      expect(onboarding.state.scaffold_pr_number).toBeNull();

      const saved = await getEngineConfig(request, owner, repo);
      expect(saved.gate_commands).toEqual([["uv", "run", "pytest", "-q"]]);
    },
  );

  test(
    "a background refetch mid-edit does not clobber the in-progress draft",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo, engineKey, baseline } = await setUpRegisteredEngine(request, token, "e2e-step2-refetch");
      await seedControlToken(page, e2eRoot);
      await stubProposal(page, owner, repo, async (route) => {
        await route.fulfill({ json: makeProposalResult(engineKey, baseline) });
      });
      await stubValidate(page, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/configure`);
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      const baseToolsEditor = page.getByTestId("connect-step2-diff-base-tools-now-editor");
      const edited = "Read\nWrite";
      await baseToolsEditor.fill(edited);
      await expect(baseToolsEditor).toHaveValue(edited);

      await page.evaluate(() => {
        Object.defineProperty(document, "hidden", { value: true, configurable: true });
        document.dispatchEvent(new Event("visibilitychange"));
        Object.defineProperty(document, "hidden", { value: false, configurable: true });
        document.dispatchEvent(new Event("visibilitychange"));
      });

      await expect(baseToolsEditor).toHaveValue(edited);
    },
  );

  test(
    "a fresh page load re-runs the proposal — the draft cache dies on reload",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo, engineKey, baseline } = await setUpRegisteredEngine(request, token, "e2e-step2-reload");
      await seedControlToken(page, e2eRoot);
      let proposeCalls = 0;
      await stubProposal(page, owner, repo, async (route) => {
        proposeCalls += 1;
        await route.fulfill({ json: makeProposalResult(engineKey, baseline) });
      });
      await stubValidate(page, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/configure`);
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      expect(proposeCalls).toBe(1);

      await page.reload();
      await expect(page.getByTestId("connect-step2-diff-table")).toBeVisible({ timeout: 15_000 });
      expect(proposeCalls).toBe(2);
    },
  );
});
