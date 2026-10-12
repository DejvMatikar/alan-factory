import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { openExploreFromHome, settleChatSession } from "./fixtures/helpers";
import { stubDefaultEngineWorld } from "./fixtures/route-stubs";
import { seedProjectEngineKey } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
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
        credential_env_name: "E2E_EXPLORE_PROVISION_GH_TOKEN",
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

function seedRepoProvision(e2eRoot: string, sessionId: string, status: string): void {
  const sessionPath = path.join(e2eRoot, "explore-chat-sessions", sessionId, "session.json");
  const session = JSON.parse(fs.readFileSync(sessionPath, "utf-8")) as Record<string, unknown>;
  session.repo_provision = status;
  fs.writeFileSync(sessionPath, JSON.stringify(session), "utf-8");
}

test.describe("explore-chat session", { tag: "@no-claude" }, () => {
  test.beforeEach(async ({ page }) => {
    await stubDefaultEngineWorld(page);
    await page.goto("/repos/DejvMatikar/alan");
  });

  test("UI review explore chat reaches summarize hand-off", async ({ page }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-ui_review").click();
    await settleChatSession(page);
    await page.getByLabel("Message").fill("Let's scope a review of the home screen.");
    await page.getByRole("button", { name: /Send/ }).click();

    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("summarize-button").click();

    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("dispatch-explore")).toBeVisible();
  });

  test("Summary opens formatted by default; the Edit/Preview toggle switches views without losing an in-progress edit", async ({
    page,
  }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await page.getByLabel("Message").fill("Let's scope a review of the home screen.");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });

    const toggle = page.getByTestId("summary-source-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveText("Edit");
    await expect(page.getByTestId("summary-preview")).toBeVisible();
    await expect(page.getByTestId("summary-textarea")).toHaveCount(0);

    await toggle.click();
    await expect(toggle).toHaveText("Preview");
    const textarea = page.getByTestId("summary-textarea");
    await expect(textarea).toBeVisible();
    await expect(textarea).toBeFocused();
    await textarea.fill("# A heading\n\nSome **bold** text.");

    await toggle.click();
    await expect(page.getByTestId("summary-preview")).toBeVisible();
    await expect(
      page.getByTestId("summary-preview").getByRole("heading", { name: "A heading" }),
    ).toBeVisible();
    await expect(toggle).toHaveText("Edit");

    await toggle.click();
    await expect(page.getByTestId("summary-textarea")).toHaveValue(
      "# A heading\n\nSome **bold** text.",
    );
    await expect(toggle).toHaveText("Preview");
  });

  test("switching to a second, independent session lands the new session's summary formatted", async ({
    page,
  }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await page.getByLabel("Message").fill("Let's scope a review of the home screen.");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await page.getByTestId("summary-source-toggle").click();
    await expect(page.getByTestId("summary-textarea")).toBeVisible();

    await page.goto("/repos/DejvMatikar/alan");
    await openExploreFromHome(page);
    await page.getByTestId("consultant-research").click();
    await settleChatSession(page);
    await page.getByLabel("Message").fill("Scope a different review.");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("summary-source-toggle")).toHaveText("Edit");
    await expect(page.getByTestId("summary-textarea")).toHaveCount(0);
  });

  test("second consultant keeps independent session via URL param", async ({ page }) => {
    await openExploreFromHome(page);
    await page.getByTestId("consultant-brainstorming").click();
    await settleChatSession(page);
    await expect(page).toHaveURL(/\/explore\/brainstorming\?(.*&)?s=[0-9a-f-]{36}/);
    const bsId = new URL(page.url()).searchParams.get("s");

    await page.goto("/repos/DejvMatikar/alan");
    await openExploreFromHome(page);
    await page.getByTestId("consultant-research").click();
    await settleChatSession(page);
    await expect(page).toHaveURL(/\/explore\/research\?(.*&)?s=[0-9a-f-]{36}/);
    const resId = new URL(page.url()).searchParams.get("s");
    expect(resId).not.toBe(bsId);
  });

  test("repo provisioning: pending shows an in-progress state, then ready proceeds normally", async ({
    page,
    request,
    e2eRoot,
  }) => {
    const createRes = await request.post("/api/explore-chat/sessions", {
      data: { consultant_key: "research", engine_key: "DejvMatikar/alan" },
    });
    expect(createRes.status()).toBe(201);
    const { session_id: sessionId } = (await createRes.json()) as { session_id: string };
    seedRepoProvision(e2eRoot, sessionId, "pending");

    await page.goto(`/explore/research?s=${sessionId}`);
    const pendingBanner = page.getByTestId("repo-provision-pending");
    await expect(pendingBanner).toBeVisible({ timeout: 10_000 });
    await expect(pendingBanner).toHaveAttribute("role", "status");

    seedRepoProvision(e2eRoot, sessionId, "ready");
    await expect(pendingBanner).not.toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("repo-provision-error")).not.toBeVisible();
    await expect(page.getByLabel("Message")).not.toBeDisabled();
  });

  test("repo provisioning: a missing credential shows a retry-capable error, never a server error", async ({
    page,
    request,
    e2eRoot,
  }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-explore-owner-${suffix}`;
    const repo = `e2e-explore-repo-${suffix}`;
    await registerForgelessCredentialEngine(request, token, owner, repo);
    const projectId = await createProject(request, `e2e-explore-provision-${suffix}`);
    seedProjectEngineKey(e2eRoot, projectId, `${owner}/${repo}`);

    const createRes = await request.post("/api/explore-chat/sessions", {
      data: {
        consultant_key: "research",
        project_id: projectId,
        engine_key: `${owner}/${repo}`,
      },
    });
    expect(createRes.status()).toBe(201);
    const { session_id: sessionId } = (await createRes.json()) as { session_id: string };

    await page.goto(`/explore/research?s=${sessionId}`);
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await page.getByLabel("Message").fill("Explore this bound repo.");
    await page.getByRole("button", { name: /Send/ }).click();

    await expect(page.getByTestId("chat-user")).toHaveCount(1);
    const errorBanner = page.getByTestId("repo-provision-error");
    await expect(errorBanner).toBeVisible({ timeout: 15_000 });
    await expect(errorBanner).toHaveAttribute("role", "alert");

    await page.getByTestId("repo-provision-retry").click();
    await expect(page.getByTestId("chat-user")).toHaveCount(2);
    await expect(errorBanner).toBeVisible({ timeout: 15_000 });
  });

  test(
    "visiting the legacy flat /explore/:consultant with more than one repository connected resolves straight to the default repository, with no ambiguous chooser",
    async ({ context, request, e2eRoot }) => {
      const owner = "e2e-explore-repo-chooser";
      const repo = "widgets";
      const res = await request.post("/api/engines", {
        data: { repo: { owner, repo } },
        headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
      });
      if (!res.ok() && res.status() !== 409) {
        throw new Error(`explore-chat-session.spec: failed to seed non-default engine (${String(res.status())})`);
      }

      const page = await context.newPage();
      await page.goto("/explore/brainstorming");
      await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
      await expect(page.getByTestId("repo-chooser-modal")).toHaveCount(0);
      await expect(page).toHaveURL(
        (u) => new URL(u).pathname === "/repos/DejvMatikar/alan/explore/brainstorming",
      );

      await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
      const sessionId = new URL(page.url()).searchParams.get("s");
      expect(sessionId).toBeTruthy();
      await expect
        .poll(
          async () =>
            (await request.get(`/api/explore-chat/sessions/${String(sessionId)}`)).status(),
          { timeout: 10_000, intervals: [200, 500, 1_000] },
        )
        .toBe(200);
      const session = await request.get(`/api/explore-chat/sessions/${String(sessionId)}`);
      expect(session.ok()).toBeTruthy();
      const body = (await session.json()) as { engine_key: string | null };
      expect(body.engine_key).toBe("DejvMatikar/alan");
      await page.close();
    },
  );
});
