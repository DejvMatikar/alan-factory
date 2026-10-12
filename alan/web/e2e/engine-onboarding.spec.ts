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

async function registerEngine(
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

async function registerForgelessCredentialEngine(
  request: APIRequestContext,
  token: string,
  owner: string,
  repo: string,
): Promise<void> {
  const slug = `${owner}/${repo}`;
  const headers = { "X-Alan-Control-Token": token };
  const secretRes = await request.post("/api/secrets", {
    data: { engine: slug, purpose: "transport", value: "e2e-placeholder-token" },
    headers,
  });
  expect(secretRes.status()).toBe(201);

  const engineRes = await request.post("/api/engines", {
    data: {
      repo: { owner, repo },
      forge: {
        repo: { owner, repo },
        credential_env_name: "E2E_ONBOARD_GH_TOKEN",
        authoritative_checks: [],
      },
      preset: "python",
    },
    headers,
  });
  expect(engineRes.status()).toBe(201);

  const deleteRes = await request.delete("/api/secrets", {
    data: { engine: slug, purpose: "transport" },
    headers,
  });
  expect(deleteRes.status()).toBe(204);
}

async function createProject(request: APIRequestContext, title: string): Promise<string> {
  const res = await request.post("/api/projects", { data: { title } });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { id: string };
  return body.id;
}

test.describe("engine onboarding (repo readiness) tile", () => {
  test(
    "surfaces the no-forge-configured error state without blanking the other tiles",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-onboard-noforge-co";
      const repo = `widget-${String(Date.now())}`;
      const engineKey = `${owner}/${repo}`;

      await registerEngine(request, token, owner, repo);
      const projectId = await createProject(request, `E2E Onboard No Forge ${String(Date.now())}`);
      seedProjectEngineKey(e2eRoot, projectId, engineKey);

      await page.goto(`/projects/${projectId}`);

      await expect(page.getByTestId("engine-ops-onboarding-error")).toContainText(
        "has no forge configured",
      );
      await expect(page.getByTestId("engine-ops-identity")).toHaveText(engineKey);
      await expect(page.getByTestId("engine-ops-pool-error")).toBeVisible();
      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted out");
    },
  );

  test(
    "renders the overall readiness chip and an Open setup link to the shared wizard step for a forge-configured engine",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-onboard-co";
      const repo = `widget-${String(Date.now())}`;
      const engineKey = `${owner}/${repo}`;

      await registerForgelessCredentialEngine(request, token, owner, repo);
      const projectId = await createProject(request, `E2E Onboard ${String(Date.now())}`);
      seedProjectEngineKey(e2eRoot, projectId, engineKey);

      await seedControlToken(page, e2eRoot);
      await page.goto(`/projects/${projectId}`);

      await expect(page.getByTestId("engine-ops-onboarding-overall")).toHaveText("Blocked");
      const setupLink = page.getByTestId("engine-ops-onboarding-setup-link");
      await expect(setupLink).toBeVisible();
      await expect(setupLink).toHaveAttribute("href", `/connect/${owner}/${repo}/dispatchable`);
    },
  );
});
