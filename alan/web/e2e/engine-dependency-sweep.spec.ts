import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedControlToken, seedProjectEngineKey, seedProjectFeatures } from "./fixtures/seed";
import { withDefaultEngineTransportCredential } from "./fixtures/transport-credential";

withDefaultEngineTransportCredential();

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

async function createProject(request: APIRequestContext, title: string): Promise<string> {
  const res = await request.post("/api/projects", { data: { title } });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { id: string };
  return body.id;
}

test.describe("engine dependency sweep tile", () => {
  test(
    "surfaces the opted-out, never-run empty state for a freshly registered engine",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-dep-sweep-co";
      const repo = `widget-${String(Date.now())}`;
      const engineKey = `${owner}/${repo}`;

      await registerEngine(request, token, owner, repo);
      const projectId = await createProject(request, `E2E Dep Sweep ${String(Date.now())}`);
      seedProjectEngineKey(e2eRoot, projectId, engineKey);

      await page.goto(`/projects/${projectId}`);

      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted out");
      await expect(page.getByTestId("engine-ops-dep-sweep-last-run")).toBeVisible();
      await expect(page.getByTestId("engine-ops-dep-sweep-next-run")).toBeVisible();
      await expect(page.getByTestId("engine-ops-dep-sweep-toggle-button")).toHaveText("Opt in");
    },
  );

  test(
    "the toggle drives the opt-in endpoint and the pill updates only via the dep_sweep_changed SSE re-read",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-dep-sweep-toggle-co";
      const repo = `widget-${String(Date.now())}`;
      const engineKey = `${owner}/${repo}`;

      await registerEngine(request, token, owner, repo);
      const projectId = await createProject(
        request,
        `E2E Dep Sweep Toggle ${String(Date.now())}`,
      );
      seedProjectEngineKey(e2eRoot, projectId, engineKey);

      await seedControlToken(page, e2eRoot);
      await page.goto(`/projects/${projectId}`);
      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted out");

      await page.getByTestId("engine-ops-dep-sweep-toggle-button").click();
      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted in", {
        timeout: 10_000,
      });
      await expect(page.getByTestId("engine-ops-dep-sweep-toggle-button")).toHaveText(
        "Opt out",
      );

      const enabledRes = await request.get(`/api/engines/${owner}/${repo}/dependency-sweep`);
      expect((await enabledRes.json()) as { enabled: boolean }).toMatchObject({ enabled: true });

      await page.getByTestId("engine-ops-dep-sweep-toggle-button").click();
      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted out", {
        timeout: 10_000,
      });

      const disabledRes = await request.get(`/api/engines/${owner}/${repo}/dependency-sweep`);
      expect((await disabledRes.json()) as { enabled: boolean }).toMatchObject({
        enabled: false,
      });
    },
  );

  test(
    "the toggle is reachable and works from the repository-scoped engine config screen with no project bound",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-dep-sweep-repo-scoped-co";
      const repo = `widget-${String(Date.now())}`;

      await registerEngine(request, token, owner, repo);
      await seedControlToken(page, e2eRoot);

      await page.goto(`/repos/${owner}/${repo}/config`);
      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted out");

      await page.getByTestId("engine-ops-dep-sweep-toggle-button").click();
      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted in", {
        timeout: 10_000,
      });

      const enabledRes = await request.get(`/api/engines/${owner}/${repo}/dependency-sweep`);
      expect((await enabledRes.json()) as { enabled: boolean }).toMatchObject({ enabled: true });
    },
  );
});
