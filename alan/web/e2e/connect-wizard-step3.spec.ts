import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { APIRequestContext, Page } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedControlToken } from "./fixtures/seed";
import { ghRemotesRoot } from "./fixtures/server-env";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

function uniqueRepo(prefix: string): { owner: string; repo: string } {
  return { owner: `${prefix}-co`, repo: `${prefix}-${String(Date.now())}` };
}

function seedBareRemote(e2eRoot: string, owner: string, repo: string): void {
  const bareDir = path.join(ghRemotesRoot(e2eRoot), owner, `${repo}.git`);
  fs.mkdirSync(path.dirname(bareDir), { recursive: true });
  execFileSync("git", ["init", "--bare", "--initial-branch=main", bareDir]);
  const scratchDir = fs.mkdtempSync(path.join(os.tmpdir(), "e2e-step3-seed-"));
  try {
    execFileSync("git", ["clone", bareDir, scratchDir]);
    execFileSync("git", ["-C", scratchDir, "symbolic-ref", "HEAD", "refs/heads/main"]);
    fs.writeFileSync(path.join(scratchDir, "README.md"), "seed\n");
    execFileSync("git", ["-C", scratchDir, "add", "-A"]);
    execFileSync("git", [
      "-C", scratchDir,
      "-c", "user.email=e2e-seed@example.com",
      "-c", "user.name=e2e-seed",
      "commit", "-m", "seed",
    ]);
    execFileSync("git", ["-C", scratchDir, "push", "origin", "HEAD:main"]);
  } finally {
    fs.rmSync(scratchDir, { recursive: true, force: true });
  }
}

function setPrState(e2eRoot: string, states: string[]): void {
  fs.writeFileSync(path.join(e2eRoot, "gh-pr-state.json"), JSON.stringify(states));
}

function setOauthScopes(e2eRoot: string, scopes: string): void {
  fs.writeFileSync(path.join(e2eRoot, "gh-oauth-scopes"), scopes);
}

function seedBareRemoteRejectingScaffoldPush(
  e2eRoot: string,
  owner: string,
  repo: string,
  rejectMessage: string,
): void {
  seedBareRemote(e2eRoot, owner, repo);
  const bareDir = path.join(ghRemotesRoot(e2eRoot), owner, `${repo}.git`);
  const hookPath = path.join(bareDir, "hooks", "pre-receive");
  const messagePath = path.join(bareDir, "hooks", "pre-receive-message.txt");
  fs.writeFileSync(messagePath, rejectMessage);
  fs.writeFileSync(hookPath, `#!/bin/sh\ncat "${messagePath}" 1>&2\nexit 1\n`);
  fs.chmodSync(hookPath, 0o755);
}

const STILL_OPEN = Array.from({ length: 50 }, () => "OPEN");
const NOW_MERGED: string[] = [];

async function seedSecret(
  request: APIRequestContext,
  token: string,
  slug: string,
  purpose: string,
  value: string,
): Promise<void> {
  const res = await request.post("/api/secrets", {
    data: { engine: slug, purpose, value },
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
  const res = await request.post("/api/engines", {
    data: {
      repo: { owner, repo },
      forge: {
        repo: { owner, repo },
        credential_env_name: "E2E_STEP3_GH_TOKEN",
        authoritative_checks: [],
      },
      preset: "python",
    },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

async function getOnboarding(
  request: APIRequestContext,
  owner: string,
  repo: string,
): Promise<{ state: { scaffold_pr_number: number | null; protection_deferred: unknown[] } }> {
  const res = await request.get(`/api/engines/${owner}/${repo}/onboarding`);
  expect(res.ok()).toBeTruthy();
  return res.json() as Promise<{
    state: { scaffold_pr_number: number | null; protection_deferred: unknown[] };
  }>;
}

async function roundTripVisibility(page: Page): Promise<void> {
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

test.describe("connect wizard — step 3 (make dispatchable)", () => {
  test(
    "the full unstarted -> PR-open -> re-tap idempotent -> merged -> protected walk",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      test.setTimeout(90_000);
      const { owner, repo } = uniqueRepo("e2e-step3-walk");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerEngine(request, token, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("wizard-rail-step-dispatchable")).toHaveAttribute(
        "aria-current",
        "step",
        { timeout: 15_000 },
      );
      await expect(page.getByTestId("step3-scaffold-status")).toHaveText("not started");
      expect(await page.getByRole("alert").count()).toBe(0);

      setPrState(e2eRoot, STILL_OPEN);
      await page.getByTestId("step3-footer-primary").click();
      await expect(page.getByTestId("step3-scaffold-status")).toHaveText(
        "waiting on you",
        { timeout: 30_000 },
      );
      const prLink = page.getByTestId("step3-scaffold-link");
      await expect(prLink).toHaveAttribute("href", /^https:\/\/github\.com\/.+\/pull\/\d+$/);
      const firstOnboarding = await getOnboarding(request, owner, repo);
      const firstPrNumber = firstOnboarding.state.scaffold_pr_number;
      expect(firstPrNumber).not.toBeNull();

      setPrState(e2eRoot, STILL_OPEN);
      await page.getByTestId("step3-footer-primary").click();
      await expect(page.getByTestId("step3-scaffold-status")).toHaveText(
        "waiting on you",
        { timeout: 30_000 },
      );
      const secondOnboarding = await getOnboarding(request, owner, repo);
      expect(secondOnboarding.state.scaffold_pr_number).toBe(firstPrNumber);

      setPrState(e2eRoot, NOW_MERGED);
      await roundTripVisibility(page);
      await expect(page.getByTestId("step3-scaffold-status")).toHaveText(
        "pass",
        { timeout: 30_000 },
      );
      await expect(
        page.getByTestId("step3-protection-squash_merge_enabled-status"),
      ).toHaveText("pass", { timeout: 30_000 });
      expect(await page.getByRole("alert").count()).toBe(0);

      const finalOnboarding = await getOnboarding(request, owner, repo);
      expect(finalOnboarding.state.scaffold_pr_number).toBe(firstPrNumber);
      expect(finalOnboarding.state.protection_deferred).toHaveLength(0);
    },
  );

  test(
    "reviewer-credential-missing hoists the mint_credentials finding as the first blocking row",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-step3-reviewer-missing");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await registerEngine(request, token, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("step3-mint_credentials")).toHaveAttribute(
        "role",
        "alert",
        { timeout: 15_000 },
      );
      await expect(page.getByTestId("step3-mint_credentials-remedy")).toContainText(
        "reviewer",
      );
      await expect(page.getByTestId("step3-mint_credentials-remedy")).toContainText(
        "Settings",
      );
      const rows = page.getByTestId("step3-checklist").locator("> div");
      await expect(rows.first()).toHaveAttribute("data-testid", "step3-mint_credentials");
    },
  );

  test(
    "re-entry after closing mid-flow (PR open, unmerged) re-derives the same waiting state",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-step3-reentry");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerEngine(request, token, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("step3-scaffold-status")).toHaveText(
        "not started",
        { timeout: 15_000 },
      );
      setPrState(e2eRoot, STILL_OPEN);
      await page.getByTestId("step3-footer-primary").click();
      await expect(page.getByTestId("step3-scaffold-status")).toHaveText(
        "waiting on you",
        { timeout: 30_000 },
      );

      setPrState(e2eRoot, STILL_OPEN);
      await page.goto(`/connect/${owner}/${repo}`);
      await expect(page.getByTestId("step3-scaffold-status")).toHaveText(
        "waiting on you",
        { timeout: 30_000 },
      );
      await expect(page.getByTestId("wizard-rail-step-dispatchable")).toHaveAttribute(
        "aria-current",
        "step",
        { timeout: 15_000 },
      );
    },
  );

  test(
    "a classic token without the workflow scope fails the workflow-write row, naming the remedy",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-step3-scope-absent");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerEngine(request, token, owner, repo);
      setOauthScopes(e2eRoot, "repo, gist");

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("step3-workflow_write-status")).toHaveText(
        "fail",
        { timeout: 15_000 },
      );
      await expect(page.getByTestId("step3-workflow_write-remedy")).toContainText(
        "workflow' checkbox",
      );
    },
  );

  test(
    "a token with the workflow scope passes the workflow-write row (unchanged happy path)",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-step3-scope-present");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerEngine(request, token, owner, repo);
      setOauthScopes(e2eRoot, "repo, workflow");

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("step3-workflow_write-status")).toHaveText(
        "pass",
        { timeout: 15_000 },
      );
      expect(await page.getByRole("alert").count()).toBe(0);
    },
  );

  test(
    "a fine-grained token (no X-OAuth-Scopes header) reads as unverifiable, not a hard fail",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-step3-scope-none");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerEngine(request, token, owner, repo);
      setOauthScopes(e2eRoot, "none");

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("step3-workflow_write-status")).toHaveText(
        "can't verify from here",
        { timeout: 15_000 },
      );
      expect(await page.getByRole("alert").count()).toBe(0);
    },
  );

  test(
    "fixing the token's scope and re-saving the credential flips the row to pass, no page reload",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-step3-scope-recover");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerEngine(request, token, owner, repo);
      setOauthScopes(e2eRoot, "repo");

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("step3-workflow_write-status")).toHaveText(
        "fail",
        { timeout: 15_000 },
      );

      setOauthScopes(e2eRoot, "repo, workflow");
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport_rescoped");

      await expect(page.getByTestId("step3-workflow_write-status")).toHaveText(
        "pass",
        { timeout: 15_000 },
      );
    },
  );

  test(
    "a rejected scaffold push surfaces the forge's own stated reason, never a raw internal enum",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-step3-push-rejected");
      const slug = `${owner}/${repo}`;
      const rejectMessage =
        "refusing to allow a Personal Access Token to create or update workflow " +
        "`.github/workflows/ci.yml` without the `workflow` scope";
      seedBareRemoteRejectingScaffoldPush(e2eRoot, owner, repo, rejectMessage);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerEngine(request, token, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await page.getByTestId("step3-footer-primary").click();
      await expect(page.getByTestId("step3-scaffold-error-detail")).toContainText(
        "without the `workflow` scope",
        { timeout: 30_000 },
      );
      await expect(page.getByTestId("step3-scaffold-error-detail")).not.toContainText(
        "PUSH_FAILED",
      );
    },
  );

  test(
    "Start planning meanwhile navigates to the generic planning surface, creating nothing here",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-step3-planning");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedControlToken(page, e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerEngine(request, token, owner, repo);

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("step3-planning-link")).toBeVisible();

      const mutations: string[] = [];
      page.on("request", (req) => {
        if (req.method() === "GET") return;
        const { pathname } = new URL(req.url());
        if (/^\/api\/(projects|engines)(\/|$)/.test(pathname)) {
          mutations.push(`${req.method()} ${pathname}`);
        }
      });
      await page.getByTestId("step3-planning-link").click();
      await expect(page).toHaveURL(/\/build(\?|$)/);
      expect(mutations).toEqual([]);
    },
  );
});
