import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedControlToken, seedProjectFeatures } from "./fixtures/seed";
import { ghRemotesRoot } from "./fixtures/server-env";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

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
  const scratchDir = fs.mkdtempSync(path.join(os.tmpdir(), "e2e-step4-seed-"));
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
        credential_env_name: "E2E_STEP4_GH_TOKEN",
        authoritative_checks: [],
      },
      preset: "python",
    },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

async function setUpRegisteredEngine(
  e2eRoot: string,
  request: APIRequestContext,
  prefix: string,
): Promise<{ owner: string; repo: string }> {
  const { owner, repo } = uniqueRepo(prefix);
  const slug = `${owner}/${repo}`;
  seedBareRemote(e2eRoot, owner, repo);
  const token = readControlToken(e2eRoot);
  await seedSecret(request, token, slug, "transport", "ghp_e2e_transport");
  await registerEngine(request, token, owner, repo);
  return { owner, repo };
}

const REGISTER_RE = /^(ready to dispatch|blocked|partially ready)$/;

test.describe("connect wizard — step 4 (posture, then finish)", () => {
  test(
    "renders a real readiness register, pre-checks observe, greys the two higher rungs with the reason in the DOM, and keyboard navigation never lands on a disabled rung",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      test.setTimeout(60_000);
      const { owner, repo } = await setUpRegisteredEngine(e2eRoot, request, "e2e-step4-radios");
      await seedControlToken(page, e2eRoot);

      await page.goto(`/connect/${owner}/${repo}/posture`);
      await expect(page.getByTestId("connect-step4-posture")).toBeVisible();
      await expect(page.getByTestId("step4-readiness-chip")).toHaveText(REGISTER_RE, {
        timeout: 30_000,
      });

      await expect(page.getByTestId("step4-posture-observe")).toBeChecked();
      await expect(page.getByTestId("step4-posture-propose")).not.toBeChecked();

      const selfMerge = page.getByTestId("step4-posture-self_merge");
      const autonomous = page.getByTestId("step4-posture-autonomous");
      await expect(selfMerge).toBeDisabled();
      await expect(autonomous).toBeDisabled();
      const reasonId = await selfMerge.getAttribute("aria-describedby");
      expect(reasonId).not.toBeNull();
      expect(await autonomous.getAttribute("aria-describedby")).toBe(reasonId);
      await expect(page.locator(`#${reasonId}`)).toContainText(/merged work/);

      await page.getByTestId("step4-posture-observe").focus();
      for (let i = 0; i < 4; i += 1) {
        await page.keyboard.press("ArrowDown");
      }
      await expect(selfMerge).not.toBeChecked();
      await expect(autonomous).not.toBeChecked();
    },
  );

  test(
    "leaving posture at observe and finishing skips the confirmation dialog and lands on the project page",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      test.setTimeout(90_000);
      const { owner, repo } = await setUpRegisteredEngine(e2eRoot, request, "e2e-step4-observe");
      await seedControlToken(page, e2eRoot);

      await page.goto(`/connect/${owner}/${repo}/posture`);
      await expect(page.getByTestId("step4-readiness-chip")).toHaveText(REGISTER_RE, {
        timeout: 30_000,
      });

      await page.getByTestId("step4-footer-primary").click();
      await expect(page.getByTestId("posture-confirm-dialog")).toHaveCount(0);
      await expect(page).toHaveURL(/\/projects\/[^/]+$/, { timeout: 30_000 });
    },
  );

  test(
    "raising to propose routes through the confirm dialog with a locked target and no free <select>, then lands on the project page",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      test.setTimeout(60_000);
      const { owner, repo } = await setUpRegisteredEngine(e2eRoot, request, "e2e-step4-raise");
      await seedControlToken(page, e2eRoot);

      await page.goto(`/connect/${owner}/${repo}/posture`);
      await expect(page.getByTestId("step4-readiness-chip")).toHaveText(REGISTER_RE, {
        timeout: 30_000,
      });

      await page.getByTestId("step4-posture-propose").check();
      await page.getByTestId("step4-footer-primary").click();

      const dialog = page.getByTestId("posture-confirm-dialog");
      await expect(dialog).toBeVisible();
      await expect(page.getByTestId("posture-confirm-target")).toContainText("Propose");
      await expect(page.getByTestId("posture-confirm-select")).toHaveCount(0);

      await page.getByTestId("posture-confirm-submit").click();
      await expect(page).toHaveURL(/\/projects\/[^/]+$/, { timeout: 30_000 });

      const engineRes = await request.get(`/api/engines/${owner}/${repo}`);
      expect((await engineRes.json()) as { posture: string }).toMatchObject({
        posture: "propose",
      });
    },
  );

  test(
    "step 4 renders no back control of its own — the browser's own back returns to step 3",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      test.setTimeout(60_000);
      const { owner, repo } = await setUpRegisteredEngine(e2eRoot, request, "e2e-step4-back");
      await seedControlToken(page, e2eRoot);

      await page.goto(`/connect/${owner}/${repo}/dispatchable`);
      await expect(page.getByTestId("connect-step3-dispatchable")).toBeVisible();

      await page.goto(`/connect/${owner}/${repo}/posture`);
      await expect(page.getByTestId("connect-step4-posture")).toBeVisible();
      await expect(
        page.getByTestId("connect-step4-posture").getByRole("link", { name: /back/i }),
      ).toHaveCount(0);
      await expect(
        page.getByTestId("connect-step4-posture").getByRole("button", { name: /back/i }),
      ).toHaveCount(0);

      await page.goBack();
      await expect(page.getByTestId("connect-step3-dispatchable")).toBeVisible();
    },
  );

  test(
    "finishing twice for the same repo, on the not-yet-verified path, reuses the same project rather than minting a duplicate",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      test.setTimeout(120_000);
      const { owner, repo } = await setUpRegisteredEngine(e2eRoot, request, "e2e-step4-refinish");
      await seedControlToken(page, e2eRoot);
      const slug = `${owner}/${repo}`;

      await page.goto(`/connect/${owner}/${repo}/posture`);
      await expect(page.getByTestId("step4-readiness-chip")).toHaveText(REGISTER_RE, {
        timeout: 30_000,
      });
      await page.getByTestId("step4-footer-primary").click();
      await expect(page).toHaveURL(/\/projects\/[^/]+$/, { timeout: 30_000 });
      const firstProjectId = new URL(page.url()).pathname.split("/projects/")[1];

      await page.goto(`/connect/${owner}/${repo}/posture`);
      await expect(page.getByTestId("step4-readiness-chip")).toHaveText(REGISTER_RE, {
        timeout: 30_000,
      });
      await page.getByTestId("step4-footer-primary").click();
      await expect(page).toHaveURL(/\/projects\/[^/]+$/, { timeout: 30_000 });
      const secondProjectId = new URL(page.url()).pathname.split("/projects/")[1];

      expect(secondProjectId).toBe(firstProjectId);

      const listRes = await request.get("/api/projects?include=archived");
      expect(listRes.ok()).toBeTruthy();
      const projects = (await listRes.json()) as {
        id: string;
        engine_key?: string | null;
        intended_engine_key?: string | null;
      }[];
      const matching = projects.filter(
        (p) => p.engine_key === slug || p.intended_engine_key === slug,
      );
      expect(matching).toHaveLength(1);
    },
  );
});
