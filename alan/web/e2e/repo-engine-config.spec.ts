import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedControlToken, seedProjectEngineKey, seedProjectFeatures } from "./fixtures/seed";
import { withDefaultEngineTransportCredential } from "./fixtures/transport-credential";

withDefaultEngineTransportCredential();

test.beforeEach(async ({ page, e2eRoot }) => {
  await seedProjectFeatures(page, true);
  await seedControlToken(page, e2eRoot);
});

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

async function registerEngine(
  request: APIRequestContext,
  token: string,
  owner: string,
  repo: string,
  extra: Record<string, unknown> = {},
): Promise<void> {
  const res = await request.post("/api/engines", {
    data: { repo: { owner, repo }, ...extra },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

test.describe("repo-scoped engine configuration screen", () => {
  test(
    "Home's Settings link lands on this repository's config screen",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo);

      await page.goto(`/repos/${owner}/${repo}`);
      await page.getByTestId(`repo-action-settings-${owner}/${repo}`).click();
      await expect(page.getByTestId("repo-engine-config-identity")).toHaveText(`${owner}/${repo}`);
      await expect(page).toHaveURL(new RegExp(`/repos/${owner}/${repo}/config$`));
    },
  );

  test(
    "Home's Settings link on the default engine's own row lands on its config screen too — no reduced variant",
    { tag: "@no-claude" },
    async ({ page }) => {
      await page.goto("/");
      await page.getByTestId("repo-action-settings-DejvMatikar/alan").click();
      await expect(page.getByTestId("repo-engine-config-identity")).toHaveText("DejvMatikar/alan");
      await expect(page.getByTestId("engine-config-readonly")).toBeVisible();
    },
  );

  test(
    "Settings from a hidden repository's row still reaches its config screen, with the hidden notice",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-hidden-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo);

      await page.goto(`/repos/${owner}/${repo}`);
      await page.getByTestId(`repo-action-hide-${owner}/${repo}`).click();
      const hideAnnouncement = page.getByTestId("hidden-repo-announcement");
      await expect(hideAnnouncement).toBeVisible();
      await expect(hideAnnouncement).not.toHaveClass(/sr-only/);

      await page.goto(`/repos/${owner}/${repo}/config`);
      await expect(page.getByTestId("repo-engine-config-hidden-notice")).toBeVisible();
    },
  );

  test(
    "direct navigation reaches the screen without visiting Home first",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-direct-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo);

      await page.goto(`/repos/${owner}/${repo}/config`);
      await expect(page.getByTestId("repo-engine-config-identity")).toHaveText(`${owner}/${repo}`);
    },
  );

  test(
    "an unregistered repository shows the purposeful empty state routing to the connect flow",
    { tag: "@no-claude" },
    async ({ page }) => {
      const owner = "e2e-repo-config-empty-co";
      const repo = `widget-${String(Date.now())}`;

      await page.goto(`/repos/${owner}/${repo}/config`);
      await expect(page.getByTestId("repo-engine-config-empty")).toBeVisible();
      await page.getByTestId("repo-engine-config-connect-cta").click();
      await expect(page).toHaveURL(new RegExp(`/connect/${owner}/${repo}`));
    },
  );

  test(
    "posture changes end-to-end with no project existing",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-posture-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo);

      await page.goto(`/repos/${owner}/${repo}/config`);
      await expect(page.getByTestId("engine-ops-posture-pill")).toHaveText("Observe");
      await page.getByTestId("engine-ops-posture-change-button").click();
      const dialog = page.getByTestId("posture-confirm-dialog");
      await expect(dialog).toBeVisible();
      await dialog.getByTestId("posture-confirm-select").selectOption("propose");
      await dialog.getByTestId("posture-confirm-submit").click();
      await expect(dialog).toBeHidden({ timeout: 10_000 });
      await expect(page.getByTestId("engine-ops-posture-pill")).toHaveText("Propose", {
        timeout: 10_000,
      });

      const detailRes = await request.get(`/api/engines/${owner}/${repo}`);
      expect(((await detailRes.json()) as { posture: string }).posture).toBe("propose");
    },
  );

  test(
    "dependency-sweep opt-in succeeds end-to-end with no project existing",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-dep-sweep-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo);

      await page.goto(`/repos/${owner}/${repo}/config`);
      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted out");
      await page.getByTestId("engine-ops-dep-sweep-toggle-button").click();
      await expect(page.getByTestId("engine-ops-dep-sweep-status")).toHaveText("Opted in", {
        timeout: 10_000,
      });

      const sweepRes = await request.get(`/api/engines/${owner}/${repo}/dependency-sweep`);
      expect((await sweepRes.json()) as { enabled: boolean }).toMatchObject({ enabled: true });
    },
  );

  test(
    "dependency-sweep opt-in is refused with an actionable finding when the roster is missing required teams",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-dep-sweep-blocked-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo, {
        transition_graph: { research: ["architecture"], architecture: [] },
      });

      await page.goto(`/repos/${owner}/${repo}/config`);
      await page.getByTestId("engine-ops-dep-sweep-toggle-button").click();
      await expect(page.getByTestId("engine-ops-dep-sweep-finding")).toBeVisible();

      const sweepRes = await request.get(`/api/engines/${owner}/${repo}/dependency-sweep`);
      expect((await sweepRes.json()) as { enabled: boolean }).toMatchObject({ enabled: false });
    },
  );

  test(
    "a keyboard-only pass reaches from the Settings link through a disclosure summary to a live dial button",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-keyboard-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo);

      await page.goto(`/repos/${owner}/${repo}`);
      const settings = page.getByTestId(`repo-action-settings-${owner}/${repo}`);
      await settings.focus();
      await page.keyboard.press("Enter");
      await expect(page.getByTestId("repo-engine-config-identity")).toHaveText(`${owner}/${repo}`);

      const disclosure = page.getByTestId("engine-config-readonly-forge-disclosure");
      await disclosure.locator("summary").focus();
      await page.keyboard.press("Enter");
      await expect(disclosure).toHaveAttribute("open", "");

      await page.getByTestId("engine-ops-posture-change-button").focus();
      await page.keyboard.press("Enter");
      await expect(page.getByTestId("posture-confirm-dialog")).toBeVisible();
    },
  );

  test(
    "the agent catalogue tile stands where the roles list and prompt overrides used to be, and opens the catalogue",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-agents-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo);

      await page.goto(`/repos/${owner}/${repo}/config`);
      await expect(page.getByTestId("agent-catalogue-tile")).toBeVisible();
      await expect(page.getByTestId("engine-config-readonly-roles")).toHaveCount(0);
      await expect(page.getByTestId("engine-config-prompt-overrides-disclosure")).toHaveCount(0);
      await expect(page.getByText("Prompt overrides")).toHaveCount(0);

      await page.getByTestId("agent-catalogue-tile-link").click();
      await expect(page).toHaveURL(new RegExp(`/repos/${owner}/${repo}/agents$`));
      await expect(page.getByTestId("repo-agents-identity")).toHaveText(`${owner}/${repo}`);
    },
  );

  test(
    "an old /projects/:projectId/engine bookmark redirects to the right repository's config screen",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      const owner = "e2e-repo-config-bookmark-co";
      const repo = `widget-${String(Date.now())}`;
      await registerEngine(request, token, owner, repo);

      const projectRes = await request.post("/api/projects", {
        data: { title: `E2E Repo Config Bookmark ${String(Date.now())}` },
      });
      const { id: projectId } = (await projectRes.json()) as { id: string };
      seedProjectEngineKey(e2eRoot, projectId, `${owner}/${repo}`);

      await page.goto(`/projects/${projectId}/engine`);
      await expect(page).toHaveURL(new RegExp(`/repos/${owner}/${repo}/config$`));
      await expect(page.getByTestId("repo-engine-config-identity")).toHaveText(`${owner}/${repo}`);
    },
  );
});
