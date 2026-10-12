import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedProjectEngineKey } from "./fixtures/seed";
import { ghRemotesRoot } from "./fixtures/server-env";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

function uniqueRepo(prefix: string): { owner: string; repo: string } {
  return { owner: `${prefix}-co`, repo: `${prefix}-${String(Date.now())}` };
}

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

async function deleteSecret(
  request: APIRequestContext,
  token: string,
  slug: string,
  purpose: string,
): Promise<void> {
  const res = await request.delete("/api/secrets", {
    data: { engine: slug, purpose },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.ok()).toBeTruthy();
}

async function registerPlainEngine(
  request: APIRequestContext,
  token: string,
  owner: string,
  repo: string,
): Promise<void> {
  const res = await request.post("/api/engines", {
    data: { repo: { owner, repo } },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

async function registerForgeEngine(
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
        credential_env_name: "E2E_HOME_STATES_GH_TOKEN",
        authoritative_checks: [],
      },
      preset: "python",
    },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

interface OnboardingSnapshot {
  state: {
    scaffold_pr_number: number | null;
    protection_applied_keys: string[];
    protection_deferred: unknown[];
  };
}

async function getOnboarding(
  request: APIRequestContext,
  owner: string,
  repo: string,
): Promise<OnboardingSnapshot> {
  const res = await request.get(`/api/engines/${owner}/${repo}/onboarding`);
  expect(res.ok()).toBeTruthy();
  return res.json() as Promise<OnboardingSnapshot>;
}

function seedBareRemote(e2eRoot: string, owner: string, repo: string): void {
  const bareDir = path.join(ghRemotesRoot(e2eRoot), owner, `${repo}.git`);
  fs.mkdirSync(path.dirname(bareDir), { recursive: true });
  execFileSync("git", ["init", "--bare", "--initial-branch=main", bareDir]);
  const scratchDir = fs.mkdtempSync(path.join(os.tmpdir(), "e2e-home-states-seed-"));
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

const STILL_OPEN = Array.from({ length: 50 }, () => "OPEN");
const NOW_MERGED: string[] = [];

const RAIL_STEP_ORDER = ["identify", "configure", "dispatchable", "posture"];

test.describe("Home connect states — blank slate + needs-you lines", () => {
  test(
    "blank slate renders the full-page card, suppresses the toolbar/default section, and its CTA navigates to /connect",
    { tag: "@no-claude" },
    async ({ page }) => {
      await page.route("**/api/engines", async (route) => {
        await route.fulfill({ json: { engines: [] } });
      });
      await page.goto("/");
      await expect(page.getByTestId("home-blank-slate")).toBeVisible();
      await expect(page.getByTestId("home-toolbar")).toHaveCount(0);
      await expect(page.getByRole("heading", { level: 1, name: "Alan" })).toHaveCount(0);

      await page.getByTestId("connect-repo-cta").click();
      await expect(page).toHaveURL(/\/connect$/);
    },
  );

  test(
    "a cold load on an install with a default engine never mounts the blank slate while the engine list is in flight",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-home-states-cold");
      await registerPlainEngine(request, readControlToken(e2eRoot), owner, repo);

      await page.addInitScript(() => {
        const watched = window as Window & { __blankSlateSeen?: boolean };
        watched.__blankSlateSeen = false;
        new MutationObserver((records) => {
          for (const record of records) {
            for (const node of record.addedNodes) {
              if (
                node instanceof Element &&
                (node.matches('[data-testid="home-blank-slate"]') ||
                  node.querySelector('[data-testid="home-blank-slate"]') !== null)
              ) {
                watched.__blankSlateSeen = true;
              }
            }
          }
        }).observe(document, { childList: true, subtree: true });
      });
      await page.route("**/api/engines", async (route) => {
        if (route.request().method() !== "GET") {
          await route.continue();
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 600));
        await route.continue();
      });

      await page.goto("/");
      await expect(page).toHaveURL(/\/repos\/DejvMatikar\/alan$/);
      await expect(page.getByTestId("home-blank-slate")).toHaveCount(0);
      const seen = await page.evaluate(
        () => (window as Window & { __blankSlateSeen?: boolean }).__blankSlateSeen,
      );
      expect(seen).toBe(false);
    },
  );

  test(
    "populated Home renders the default section and its own drained/failed-fetch registers, distinct from the blank slate",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-home-states-pop");
      const token = readControlToken(e2eRoot);
      await registerPlainEngine(request, token, owner, repo);

      let failWorkspaces = false;
      await page.route("**/api/workspaces**", async (route) => {
        if (failWorkspaces) {
          await route.fulfill({ status: 500, json: { detail: "boom" } });
          return;
        }
        await route.fulfill({ json: [] });
      });
      await page.route("**/api/projects**", async (route) => {
        await route.fulfill({ json: [] });
      });

      await page.goto("/");
      await expect(page.getByTestId("home-blank-slate")).toHaveCount(0);
      await expect(page.getByRole("heading", { level: 1, name: "Alan" })).toBeVisible();
      await expect(page.getByTestId("repo-empty-DejvMatikar/alan")).toHaveText(
        "No active workspaces yet.",
      );

      failWorkspaces = true;
      await page.reload();
      await expect(page.getByTestId("home-load-error")).toBeVisible();
      await expect(page.getByTestId("home-blank-slate")).toHaveCount(0);
    },
  );

  test(
    "an identify-incomplete repo yields exactly one resume line reading step 1, and clicking it lands the wizard on step 1",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-home-states-identify");
      const slug = `${owner}/${repo}`;
      const token = readControlToken(e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await registerForgeEngine(request, token, owner, repo);
      await deleteSecret(request, token, slug, "transport");

      await page.goto("/repos/DejvMatikar/alan");
      const line = page.getByTestId(`needs-you-resume-${owner}-${repo}`);
      await expect(line).toBeVisible();
      await expect(line).toHaveText(`Finish setup of ${owner}/${repo} — step 1 →`);

      await line.click();
      await expect(page).toHaveURL(new RegExp(`/connect/${owner}/${repo}$`));
      await expect(page.getByTestId("wizard-rail-step-identify")).toHaveAttribute(
        "aria-current",
        "step",
      );
    },
  );

  test(
    "a mid-setup repo's resume line always names the step the bare URL actually lands on",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-home-states-configure");
      const slug = `${owner}/${repo}`;
      const token = readControlToken(e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await registerForgeEngine(request, token, owner, repo);

      await page.goto("/repos/DejvMatikar/alan");
      const line = page.getByTestId(`needs-you-resume-${owner}-${repo}`);
      await expect(line).toBeVisible();
      const text = await line.textContent();
      const match = /step (\d+)/.exec(text ?? "");
      if (match === null) throw new Error(`resume line text did not name a step: ${String(text)}`);
      const labeledStep = Number(match[1]);
      const labeledStepKey = RAIL_STEP_ORDER[labeledStep - 1];
      if (labeledStepKey === undefined) throw new Error(`unexpected labeled step number: ${String(labeledStep)}`);

      await line.click();
      await expect(page).toHaveURL(new RegExp(`/connect/${owner}/${repo}$`));
      await expect(page.getByTestId(`wizard-rail-step-${labeledStepKey}`)).toHaveAttribute(
        "aria-current",
        "step",
      );
    },
  );

  test(
    "two mid-setup repos yield exactly two resume lines",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const repos = [
        uniqueRepo("e2e-home-states-two-a"),
        uniqueRepo("e2e-home-states-two-b"),
      ];
      for (const { owner, repo } of repos) {
        const slug = `${owner}/${repo}`;
        await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
        await registerForgeEngine(request, token, owner, repo);
        await deleteSecret(request, token, slug, "transport");
      }

      await page.goto("/repos/DejvMatikar/alan");
      for (const { owner, repo } of repos) {
        await expect(page.getByTestId(`needs-you-resume-${owner}-${repo}`)).toBeVisible();
      }
    },
  );

  test(
    "a fully set-up repo (scaffold merged and protected) yields no resume line",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      test.setTimeout(60_000);
      const { owner, repo } = uniqueRepo("e2e-home-states-done");
      const slug = `${owner}/${repo}`;
      seedBareRemote(e2eRoot, owner, repo);
      const token = readControlToken(e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await seedSecret(request, token, slug, "reviewer", "ghp_e2e_reviewer");
      await registerForgeEngine(request, token, owner, repo);

      setPrState(e2eRoot, STILL_OPEN);
      const scaffoldRes = await request.post(`/api/engines/${owner}/${repo}/scaffold`, {
        headers: { "X-Alan-Control-Token": token },
      });
      expect(scaffoldRes.ok()).toBeTruthy();

      setPrState(e2eRoot, NOW_MERGED);
      await expect(async () => {
        const onboarding = await getOnboarding(request, owner, repo);
        if (
          onboarding.state.protection_deferred.length > 0 ||
          onboarding.state.protection_applied_keys.length === 0
        ) {
          const protectRes = await request.post(`/api/engines/${owner}/${repo}/protect`, {
            headers: { "X-Alan-Control-Token": token },
          });
          expect(protectRes.ok()).toBeTruthy();
        }
        expect(onboarding.state.protection_deferred).toHaveLength(0);
        expect(onboarding.state.protection_applied_keys.length).toBeGreaterThan(0);
      }).toPass({ timeout: 30_000 });

      await page.goto("/repos/DejvMatikar/alan");
      await expect(page.getByTestId(`needs-you-resume-${owner}-${repo}`)).toHaveCount(0);
    },
  );

  test(
    "no control token: the host-setup line appears, clicking it opens Settings, and setting the token there makes it disappear without reload",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const { owner, repo } = uniqueRepo("e2e-home-states-hostsetup");
      await seedSecret(request, token, `${owner}/${repo}`, "transport", "ghp_e2e_placeholder");

      await page.goto("/repos/DejvMatikar/alan");
      const hostLine = page.getByTestId("needs-you-host-setup");
      await expect(hostLine).toBeVisible();
      await expect(hostLine).toHaveText("Finish host setup — control token not set");

      await hostLine.click();
      await expect(page.getByTestId("settings-modal")).toBeVisible();
      await page.getByTestId("control-token-input").fill("e2e-fake-token-value");
      await page.getByTestId("save-token").click();
      await page.getByTestId("settings-modal-close").click();
      await expect(page.getByTestId("needs-you-host-setup")).toHaveCount(0);
    },
  );

  test(
    "a mid-setup repo with a bound project renders zero resume lines — the bound-project arm of the operational-evidence exclusion",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-home-states-boundproj");
      const slug = `${owner}/${repo}`;
      const token = readControlToken(e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await registerForgeEngine(request, token, owner, repo);

      const createRes = await request.post("/api/projects", {
        data: { title: `${repo} project` },
      });
      expect(createRes.ok()).toBeTruthy();
      const { id: projectId } = (await createRes.json()) as { id: string };
      seedProjectEngineKey(e2eRoot, projectId, slug);

      await page.goto("/repos/DejvMatikar/alan");
      await expect(page.getByTestId(`needs-you-resume-${owner}-${repo}`)).toHaveCount(0);
    },
  );

  test(
    "a mid-setup repo whose only association is a project recording intent, not binding, still renders its resume line",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-home-states-intentproj");
      const slug = `${owner}/${repo}`;
      const token = readControlToken(e2eRoot);
      await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
      await registerForgeEngine(request, token, owner, repo);

      const createRes = await request.post("/api/projects", {
        data: { title: `${repo} project`, intended_engine_key: slug },
      });
      expect(createRes.ok()).toBeTruthy();

      await page.goto("/repos/DejvMatikar/alan");
      await expect(page.getByTestId(`needs-you-resume-${owner}-${repo}`)).toBeVisible();
    },
  );
});
