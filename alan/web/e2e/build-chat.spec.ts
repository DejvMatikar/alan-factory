import fs from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import {
  clearHangFile,
  ensureHangFile,
  settleChatSession,
  startBuildFromHome,
} from "./fixtures/helpers";
import { stubDefaultEngineWorld, stubDefaultProjectWorld } from "./fixtures/route-stubs";
import { seedProjectFeatures } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

test.beforeEach(async ({ page }) => {
  await stubDefaultProjectWorld(page);
  await stubDefaultEngineWorld(page);
});

test("The Build shortcut navigates to /build", { tag: "@no-claude" }, async ({ page }) => {
  await page.goto("/repos/DejvMatikar/alan");
  await startBuildFromHome(page);
  await expect(page).toHaveURL(/\/build/);
  await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
});

test("build page shows Chat and Summary tabs", { tag: "@no-claude" }, async ({ page }) => {
  await page.goto("/build");
  await expect(page.getByTestId("tab-chat")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("tab-summary")).toBeVisible();
  await expect(page.getByTestId("tab-questions")).toHaveCount(0);
});

test(
  "Chat/Summary expose tab semantics and respond to arrow-key navigation",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/build");
    await expect(page).toHaveURL(/[?&]s=/);
    const chatTab = page.getByTestId("tab-chat");
    const summaryTab = page.getByTestId("tab-summary");
    await expect(chatTab).toBeVisible({ timeout: 10_000 });

    await expect(page.getByRole("tablist")).toBeVisible();
    await expect(chatTab).toHaveAttribute("aria-selected", "true");
    await expect(summaryTab).toHaveAttribute("aria-selected", "false");

    await chatTab.focus();
    await expect(chatTab).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(summaryTab).toHaveAttribute("aria-selected", "true");
    await expect(summaryTab).toBeFocused();

    await page.keyboard.press("ArrowLeft");
    await expect(chatTab).toHaveAttribute("aria-selected", "true");
  },
);

test("build page creates a session and updates URL ?s= param", { tag: "@no-claude" }, async ({
  page,
}) => {
  await page.goto("/build");
  await settleChatSession(page);
  await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
  await expect(page).toHaveURL(/\/build\?s=[0-9a-f]{8}-[0-9a-f]{4}-/);
});

test(
  "composer textarea shows the authored focus ring instead of the native UA outline",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/repos/DejvMatikar/alan");
    await startBuildFromHome(page);
    await settleChatSession(page);
    const textarea = page.getByLabel("Message");
    await expect(textarea).toBeVisible({ timeout: 10_000 });
    await expect(textarea).toBeFocused();
    const style = await textarea.evaluate((el) => {
      const computed = getComputedStyle(el);
      return { outlineStyle: computed.outlineStyle, boxShadow: computed.boxShadow };
    });
    expect(style.outlineStyle).toBe("none");
    expect(style.boxShadow).not.toBe("none");
  },
);

test(
  "Chat transcript no longer creates its own scroll region — the whole page scrolls",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/build");
    await expect(page.getByTestId("chat-transcript")).toBeVisible({ timeout: 10_000 });
    const overflowY = await page
      .getByTestId("chat-transcript")
      .evaluate((el) => getComputedStyle(el).overflowY);
    expect(overflowY).not.toBe("auto");
    expect(overflowY).not.toBe("scroll");
  },
);

test(
  "Summary opens formatted by default; the Edit/Preview toggle, reachable by keyboard, switches views without losing an in-progress edit",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });

    const toggle = page.getByTestId("summary-source-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveText("Edit");
    await expect(toggle).not.toHaveAttribute("aria-pressed");
    await expect(page.getByTestId("summary-preview")).toBeVisible();
    await expect(page.getByTestId("summary-textarea")).toHaveCount(0);

    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(toggle).toHaveText("Preview");
    await expect(toggle).not.toHaveAttribute("aria-pressed");
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
    await expect(toggle).not.toHaveAttribute("aria-pressed");
    await expect(page.getByTestId("summary-textarea")).toHaveCount(0);

    await toggle.click();
    await expect(page.getByTestId("summary-textarea")).toHaveValue(
      "# A heading\n\nSome **bold** text.",
    );
    await expect(toggle).toHaveText("Preview");
    await expect(toggle).not.toHaveAttribute("aria-pressed");
  },
);

function summarizeResponse(page: Page) {
  return page.waitForResponse(
    (res) =>
      /\/api\/build-chat\/sessions\/[^/]+\/summarize$/.test(res.url()) &&
      res.request().method() === "POST",
    { timeout: 30_000 },
  );
}

test(
  "A fresh Summarize resets the toggle to formatted even if the prior summary was left in edit mode",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
    const firstSummarize = summarizeResponse(page);
    await page.getByTestId("summarize-button").click();
    expect((await firstSummarize).ok()).toBeTruthy();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await page.getByTestId("summary-source-toggle").click();
    await expect(page.getByTestId("summary-textarea")).toBeVisible();

    await page.getByTestId("tab-chat").click();
    const secondSummarize = summarizeResponse(page);
    await page.getByTestId("summarize-button").click();
    expect((await secondSummarize).ok()).toBeTruthy();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("summary-source-toggle")).toHaveText("Edit");
    await expect(page.getByTestId("summary-textarea")).toHaveCount(0);
  },
);

test("Summarize distils a build brief with no file-level detail", async ({ page }) => {
  await page.goto("/build");
  await settleChatSession(page);
  await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
  await page.getByTestId("summarize-button").click();

  await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
    timeout: 15_000,
  });
  const summary = page.getByTestId("summary-preview");
  await expect(summary).toBeVisible();
  const content = await summary.innerText();
  expect(content.length).toBeGreaterThan(0);
  expect(content).not.toMatch(/\.(py|tsx?|jsx?)\b/);
});

test("propose recovers colon-in-acceptance-clause from stub YAML", async ({ request }) => {
  const create = await request.post("/api/build-chat/sessions", {
    data: { engine_key: "DejvMatikar/alan" },
  });
  expect(create.status()).toBe(201);
  const { session_id } = (await create.json()) as { session_id: string };

  const propose = await request.post(`/api/build-chat/sessions/${session_id}/propose`);
  expect(propose.status()).toBe(200);
  const body = (await propose.json()) as {
    status: string;
    proposal: { acceptance_clauses: string[] };
  };
  expect(body.status).toBe("ready");
  expect(body.proposal.acceptance_clauses).toContain("Colon clause: with colon");
});

test("Build plan dispatches an implementation_plan workspace and navigates home", async ({ page }) => {
  await page.goto("/build");
  await settleChatSession(page);
  await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });

  let proposeCalls = 0;
  await page.route(/\/api\/build-chat\/sessions\/[^/]+\/propose$/, async (route) => {
    proposeCalls += 1;
    await route.continue();
  });

  await page.getByTestId("summarize-button").click();
  await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
    timeout: 15_000,
  });

  await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("dispatch-counter-case")).toBeVisible();

  const dispatchPost = page.waitForRequest(
    (req) => new URL(req.url()).pathname === "/api/workspaces" && req.method() === "POST",
    { timeout: 15_000 },
  );
  await page.getByTestId("dispatch-proposal-confirm").click();
  const req = await dispatchPost;
  const body = req.postDataJSON() as { team: string };
  expect(body.team).toBe("implementation_plan");

  await expect(page).toHaveURL((u) => new URL(u).pathname === "/", { timeout: 15_000 });
  expect(proposeCalls).toBe(1);
});

test(
  "Build plan panel renders the live collision verdict, with no extra check from reaching the ready state, and re-run reuses /api/collision-check",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });

    let collisionCalls = 0;
    await page.route(/\/api\/collision-check$/, async (route) => {
      collisionCalls += 1;
      await route.fulfill({
        status: 200,
        json: { state: "green", sentence: "No overlapping work found.", match: null },
      });
    });

    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await expect.poll(() => collisionCalls, { timeout: 10_000 }).toBeGreaterThanOrEqual(1);
    const baselineCalls = collisionCalls;

    await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 15_000 });
    expect(collisionCalls).toBe(baselineCalls);
    await expect(page.getByTestId("collision-sentence")).toHaveText(
      "No overlapping work found.",
    );

    await page.getByTestId("collision-rerun").click();
    await expect.poll(() => collisionCalls, { timeout: 10_000 }).toBe(baselineCalls + 1);
  },
);

function readyProposalFixture(title: string) {
  return {
    status: "ready",
    proposal: {
      team: "implementation_plan",
      rationale: "r",
      instructions: null,
      scope_audit: [],
      title,
      acceptance_clauses: [],
      counter_case: { against: "a", cheapest_80: "b", cost_estimate: "c" },
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  };
}

test(
  "Build plan panel renders automatically once the eager prefetch resolves — no click, exactly one propose call",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });

    let proposeCalls = 0;
    await page.route(/\/api\/build-chat\/sessions\/[^/]+\/propose$/, async (route) => {
      proposeCalls += 1;
      await route.fulfill({ status: 200, json: readyProposalFixture("prefetched-title") });
    });

    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 2_000 });
    expect(proposeCalls).toBe(1);
  },
);

test(
  "Editing the brief after Summarize goes stale — Re-propose reproposes, and dispatch never sends the stale prefetched proposal",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });

    let proposeCalls = 0;
    await page.route(/\/api\/build-chat\/sessions\/[^/]+\/propose$/, async (route) => {
      proposeCalls += 1;
      const title = proposeCalls === 1 ? "stale-title" : "fresh-title";
      await route.fulfill({ status: 200, json: readyProposalFixture(title) });
    });

    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 10_000 });
    expect(proposeCalls).toBe(1);

    await page.getByTestId("summary-source-toggle").click();
    const textarea = page.getByTestId("summary-textarea");
    await textarea.fill("an edited brief");
    await textarea.blur();

    await expect(page.getByTestId("dispatch-proposal-repropose")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("dispatch-proposal-confirm")).toHaveCount(0);
    await page.getByTestId("dispatch-proposal-repropose").click();
    await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 10_000 });

    const dispatchPost = page.waitForRequest(
      (req) => new URL(req.url()).pathname === "/api/workspaces" && req.method() === "POST",
      { timeout: 15_000 },
    );
    await page.getByTestId("dispatch-proposal-confirm").click();
    const req = await dispatchPost;
    const body = req.postDataJSON() as { title: string; description: string };
    expect(body.title).toBe("fresh-title");
    expect(body.description).toBe("an edited brief");
    expect(proposeCalls).toBe(2);
  },
);

test(
  "sending a chat message right after Summarize does not wait for the still-pending background prefetch",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });

    let interruptCalls = 0;
    await page.route(/\/api\/build-chat\/sessions\/[^/]+\/propose$/, async () => {
      await new Promise(() => undefined);
    });
    await page.route(/\/api\/build-chat\/sessions\/[^/]+\/interrupt$/, async (route) => {
      interruptCalls += 1;
      await route.fulfill({ status: 204 });
    });

    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });

    await page.getByTestId("tab-chat").click();
    await page.getByLabel("Message").fill("a follow-up question");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("chat-user")).toHaveText("a follow-up question", {
      timeout: 15_000,
    });
    expect(interruptCalls).toBeGreaterThanOrEqual(1);
  },
);

const REPO_CHOOSER_FIXTURE_OWNER = "e2e-build-chat-repo-chooser";
const REPO_CHOOSER_FIXTURE_REPO = "widgets";
const REPO_CHOOSER_FIXTURE_SLUG = `${REPO_CHOOSER_FIXTURE_OWNER}/${REPO_CHOOSER_FIXTURE_REPO}`;

test(
  "visiting the legacy flat /build with more than one repository connected resolves straight to the default repository, with no ambiguous chooser",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const res = await request.post("/api/engines", {
      data: { repo: { owner: REPO_CHOOSER_FIXTURE_OWNER, repo: REPO_CHOOSER_FIXTURE_REPO } },
      headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
    });
    if (!res.ok() && res.status() !== 409) {
      throw new Error(`build-chat.spec: failed to seed non-default engine (${String(res.status())})`);
    }
    await page.unroute("**/api/engines");

    await page.goto("/build");
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("repo-chooser-modal")).toHaveCount(0);
    await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan/build");

    await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
    const sessionId = new URL(page.url()).searchParams.get("s");
    expect(sessionId).toBeTruthy();
    await expect
      .poll(
        async () => (await request.get(`/api/build-chat/sessions/${String(sessionId)}`)).status(),
        { timeout: 10_000, intervals: [200, 500, 1_000] },
      )
      .toBe(200);
    const session = await request.get(`/api/build-chat/sessions/${String(sessionId)}`);
    expect(session.ok()).toBeTruthy();
    const body = (await session.json()) as { engine_key: string | null };
    expect(body.engine_key).toBe("DejvMatikar/alan");
  },
);

test(
  "the rail's Build control for a non-default repository still binds that repository directly, with no chooser",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const res = await request.post("/api/engines", {
      data: { repo: { owner: REPO_CHOOSER_FIXTURE_OWNER, repo: REPO_CHOOSER_FIXTURE_REPO } },
      headers: { "X-Alan-Control-Token": readControlToken(e2eRoot) },
    });
    if (!res.ok() && res.status() !== 409) {
      throw new Error(`build-chat.spec: failed to seed non-default engine (${String(res.status())})`);
    }
    await page.unroute("**/api/engines");

    await page.goto(`/repos/${REPO_CHOOSER_FIXTURE_SLUG}`);
    await page.getByTestId(`rail-start-build-${REPO_CHOOSER_FIXTURE_SLUG}`).click();
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("repo-chooser-modal")).toHaveCount(0);

    await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
    const sessionId = new URL(page.url()).searchParams.get("s");
    const session = await request.get(`/api/build-chat/sessions/${String(sessionId)}`);
    expect(session.ok()).toBeTruthy();
    const body = (await session.json()) as { engine_key: string | null };
    expect(body.engine_key).toBe(REPO_CHOOSER_FIXTURE_SLUG);
  },
);

test(
  "declining in-chat delete leaves the chat exactly as it was",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await page.getByLabel("Message").fill("keep this chat");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("chat-user")).toHaveText("keep this chat", { timeout: 15_000 });

    await page.getByTestId("delete-chat-button").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();

    await page.reload();
    await expect(page.getByTestId("chat-user")).toHaveText("keep this chat", { timeout: 15_000 });
    await expect(page.getByTestId("tab-chat")).toHaveAttribute("aria-selected", "true");
  },
);

test(
  "confirming in-chat delete lands on home and removes the chat, minting no new draft session",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
    const sessionId = new URL(page.url()).searchParams.get("s");
    expect(sessionId).toBeTruthy();

    const before = await request.get("/api/build-chat/sessions");
    const beforeCount = ((await before.json()) as unknown[]).length;

    await page.getByTestId("delete-chat-button").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Delete chat" }).click();
    await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan", {
      timeout: 15_000,
    });

    const after = await request.get("/api/build-chat/sessions");
    const afterList = (await after.json()) as { session_id: string }[];
    expect(afterList.some((s) => s.session_id === sessionId)).toBe(false);
    expect(afterList.length).toBe(beforeCount - 1);
  },
);

test(
  "deleting mid-stream stops the run first, then removes the chat with no lingering activity",
  { tag: "@no-claude" },
  async ({ page, e2eRoot, request }) => {
    const hangFile = path.join(e2eRoot, "stub-hang");
    const marker = `build-delete-mid-stream-${String(Date.now())}`;
    ensureHangFile(hangFile, marker);
    let sessionId: string | null = null;
    try {
      await page.goto("/build");
      await settleChatSession(page);
      await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
      await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
      sessionId = new URL(page.url()).searchParams.get("s");
      await page.getByLabel("Message").fill(marker);
      await page.getByRole("button", { name: /Send/ }).click();
      await expect(page.getByTestId("stop-button")).toBeVisible({ timeout: 15_000 });

      await page.getByTestId("delete-chat-button").click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("button", { name: "Delete chat" }).click();
      await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan", {
        timeout: 15_000,
      });
    } finally {
      clearHangFile(hangFile);
    }

    const after = await request.get("/api/build-chat/sessions");
    const afterList = (await after.json()) as { session_id: string }[];
    expect(afterList.some((s) => s.session_id === sessionId)).toBe(false);
  },
);

function seedRepoProvision(e2eRoot: string, sessionId: string, status: string): void {
  const sessionPath = path.join(e2eRoot, "build-chat-sessions", sessionId, "session.json");
  const session = JSON.parse(fs.readFileSync(sessionPath, "utf-8")) as Record<string, unknown>;
  session.repo_provision = status;
  fs.writeFileSync(sessionPath, JSON.stringify(session), "utf-8");
}

test(
  "an alan-bound build chat shows no repo-provisioning banner",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const createRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: "DejvMatikar/alan" },
    });
    expect(createRes.status()).toBe(201);
    const { session_id: sessionId } = (await createRes.json()) as { session_id: string };

    await page.goto(`/build?s=${sessionId}`);
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("repo-provision-pending")).not.toBeVisible();
    await expect(page.getByTestId("repo-provision-error")).not.toBeVisible();
  },
);

test(
  "a pending repo_provision shows an in-progress status banner",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const createRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: "DejvMatikar/alan" },
    });
    expect(createRes.status()).toBe(201);
    const { session_id: sessionId } = (await createRes.json()) as { session_id: string };
    seedRepoProvision(e2eRoot, sessionId, "pending");

    await page.goto(`/build?s=${sessionId}`);
    const pendingBanner = page.getByTestId("repo-provision-pending");
    await expect(pendingBanner).toBeVisible({ timeout: 10_000 });
    await expect(pendingBanner).toHaveAttribute("role", "status");
  },
);

test(
  "an error repo_provision shows a retry-capable alert banner that resends the last message",
  async ({ page, request, e2eRoot }) => {
    const createRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: "DejvMatikar/alan" },
    });
    expect(createRes.status()).toBe(201);
    const { session_id: sessionId } = (await createRes.json()) as { session_id: string };

    await page.goto(`/build?s=${sessionId}`);
    await page.getByLabel("Message").fill("build this thing");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("chat-user")).toHaveCount(1, { timeout: 15_000 });
    await expect(page.getByTestId("chat-assistant")).toHaveCount(1, { timeout: 15_000 });

    seedRepoProvision(e2eRoot, sessionId, "error");
    await page.reload();

    const errorBanner = page.getByTestId("repo-provision-error");
    await expect(errorBanner).toBeVisible({ timeout: 10_000 });
    await expect(errorBanner).toHaveAttribute("role", "alert");

    await page.getByTestId("repo-provision-retry").click();
    await expect(page.getByTestId("chat-user")).toHaveCount(2, { timeout: 15_000 });
  },
);

test(
  "a failed in-chat delete keeps the person in the chat with an inline error",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 10_000 });
    const sessionId = new URL(page.url()).searchParams.get("s");

    await page.route(
      new RegExp(`/api/build-chat/sessions/${String(sessionId)}$`),
      async (route) => {
        if (route.request().method() !== "DELETE") {
          await route.continue();
          return;
        }
        await route.fulfill({ status: 500, json: { detail: "delete failed" } });
      },
    );

    await page.getByTestId("delete-chat-button").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Delete chat" }).click();

    await expect(page.getByText(/delete failed/)).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(new RegExp(`/build\\?s=${String(sessionId)}`));
    await expect(page.getByTestId("delete-chat-button")).toBeEnabled();
  },
);

test(
  "Auto dispatch runs summarize, collision check, and propose in one click and dispatches a workspace",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("auto-dispatch-button")).toBeEnabled({ timeout: 10_000 });

    let collisionCalls = 0;
    await page.route(/\/api\/collision-check$/, async (route) => {
      collisionCalls += 1;
      await route.fulfill({
        status: 200,
        json: { state: "green", sentence: "No overlapping work found.", match: null },
      });
    });
    const dispatchPost = page.waitForRequest(
      (req) => new URL(req.url()).pathname === "/api/workspaces" && req.method() === "POST",
      { timeout: 20_000 },
    );

    await page.getByTestId("auto-dispatch-button").click();
    const req = await dispatchPost;
    const body = req.postDataJSON() as { team: string };
    expect(body.team).toBe("implementation_plan");

    await expect(page).toHaveURL((u) => new URL(u).pathname === "/", { timeout: 15_000 });
    expect(collisionCalls).toBeGreaterThanOrEqual(1);
  },
);

test(
  "Auto dispatch stops without dispatching when the overlap check is not clear",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("auto-dispatch-button")).toBeEnabled({ timeout: 10_000 });

    await page.route(/\/api\/collision-check$/, async (route) => {
      await route.fulfill({
        status: 200,
        json: { state: "red", sentence: "Live overlap with a concurrent workspace.", match: null },
      });
    });
    let workspaceCalls = 0;
    await page.route(/\/api\/workspaces$/, async (route) => {
      if (route.request().method() === "POST") workspaceCalls += 1;
      await route.continue();
    });

    await page.getByTestId("auto-dispatch-button").click();
    await expect(page.getByTestId("auto-dispatch-status")).toContainText(/overlap check/, {
      timeout: 20_000,
    });
    expect(workspaceCalls).toBe(0);
    expect(new URL(page.url()).pathname).toMatch(/\/build$/);
  },
);

test(
  "Auto dispatch stops without dispatching when the proposal still has open questions",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("auto-dispatch-button")).toBeEnabled({ timeout: 10_000 });

    await page.route(/\/api\/build-chat\/sessions\/[^/]+\/propose$/, async (route) => {
      await route.fulfill({
        status: 200,
        json: { status: "need_more_info", questions: ["Which storage backend?"] },
      });
    });
    let workspaceCalls = 0;
    await page.route(/\/api\/workspaces$/, async (route) => {
      if (route.request().method() === "POST") workspaceCalls += 1;
      await route.continue();
    });

    await page.getByTestId("auto-dispatch-button").click();
    await expect(page.getByTestId("auto-dispatch-status")).toContainText(/open questions/, {
      timeout: 20_000,
    });
    expect(workspaceCalls).toBe(0);
  },
);

test(
  "Auto dispatch can be abandoned with the Stop control before it dispatches",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("auto-dispatch-button")).toBeEnabled({ timeout: 10_000 });

    await page.route(/\/api\/build-chat\/sessions\/[^/]+\/propose$/, () => new Promise(() => undefined));
    let workspaceCalls = 0;
    await page.route(/\/api\/workspaces$/, async (route) => {
      if (route.request().method() === "POST") workspaceCalls += 1;
      await route.continue();
    });

    await page.getByTestId("auto-dispatch-button").click();
    await expect(page.getByTestId("auto-dispatch-stop")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("auto-dispatch-stop").click();
    await expect(page.getByTestId("auto-dispatch-status")).toContainText(/cancelled/);
    expect(workspaceCalls).toBe(0);
  },
);

test(
  "Auto dispatch stops rather than dispatch a proposal generated against different text when the summary is edited mid-run",
  async ({ page }) => {
    await page.goto("/build");
    await settleChatSession(page);
    await expect(page.getByTestId("auto-dispatch-button")).toBeEnabled({ timeout: 10_000 });

    await page.route(/\/api\/build-chat\/sessions\/[^/]+\/propose$/, () => new Promise(() => undefined));
    let workspaceCalls = 0;
    await page.route(/\/api\/workspaces$/, async (route) => {
      if (route.request().method() === "POST") workspaceCalls += 1;
      await route.continue();
    });

    await page.getByTestId("auto-dispatch-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await page.getByTestId("summary-source-toggle").click();
    const textarea = page.getByTestId("summary-textarea");
    await expect(textarea).toBeVisible();
    await textarea.type("!");

    await expect(page.getByTestId("auto-dispatch-status")).toContainText(/summary changed/, {
      timeout: 20_000,
    });
    expect(workspaceCalls).toBe(0);
  },
);
