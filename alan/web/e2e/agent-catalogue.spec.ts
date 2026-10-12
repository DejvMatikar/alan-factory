import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext, Page } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedControlToken, seedProjectFeatures } from "./fixtures/seed";

const IMPLEMENTER_ID = "team_role:implementation/implementer";

test.beforeEach(async ({ page, e2eRoot }) => {
  await seedProjectFeatures(page, true);
  await seedControlToken(page, e2eRoot);
});

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

async function registerEngine(request: APIRequestContext, root: string): Promise<string> {
  const owner = "e2e-agent-catalogue-co";
  const repo = `widget-${String(Date.now())}-${String(Math.floor(Math.random() * 1_000_000))}`;
  const res = await request.post("/api/engines", {
    data: { repo: { owner, repo } },
    headers: { "X-Alan-Control-Token": readControlToken(root) },
  });
  expect(res.status()).toBe(201);
  return `${owner}/${repo}`;
}

function shownCount(text: string | null): { shown: number; total: number } {
  const match = /Showing (\d+) of (\d+) agents/.exec(text ?? "");
  if (match === null) throw new Error(`unexpected result count text: ${String(text)}`);
  return { shown: Number(match[1]), total: Number(match[2]) };
}

async function openCatalogue(page: Page, engineKey: string, search = ""): Promise<void> {
  await page.goto(`/repos/${engineKey}/agents${search}`);
  await expect(page.getByTestId("repo-agents-list")).toBeVisible();
}

test.describe("agent catalogue", () => {
  test(
    "the engine page's agent tile summarises the agents and opens the catalogue, with no prompt overrides section",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const engineKey = await registerEngine(request, e2eRoot);

      await page.goto(`/repos/${engineKey}/config`);
      await expect(page.getByTestId("agent-catalogue-tile-models")).toContainText("agents");
      await expect(page.getByText("Prompt overrides")).toHaveCount(0);
      await expect(page.getByTestId("engine-config-readonly-roles")).toHaveCount(0);

      await page.getByTestId("agent-catalogue-tile-link").click();
      await expect(page).toHaveURL(new RegExp(`/repos/${engineKey}/agents$`));
      await expect(page.getByRole("heading", { level: 1, name: "Agent catalogue" })).toBeVisible();
      await expect(page.getByTestId("repo-agents-appended-note")).toContainText("Claude Code");
    },
  );

  test(
    "searching prompt text narrows the list with per-prompt counts and survives a reload through the URL",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const engineKey = await registerEngine(request, e2eRoot);
      await openCatalogue(page, engineKey);
      const before = shownCount(await page.getByTestId("agent-search-count").textContent());
      expect(before.total).toBeGreaterThan(10);

      await page.getByTestId("agent-search-input").fill("verifier");
      await expect(page).toHaveURL(/[?&]q=verifier/);
      await expect
        .poll(async () => shownCount(await page.getByTestId("agent-search-count").textContent()).shown)
        .toBeLessThan(before.total);
      await expect(page.getByTestId(/^agent-row-.*-summary$/).first()).toContainText("in prompt");

      await page.reload();
      await expect(page.getByTestId("agent-search-input")).toHaveValue("verifier");
      await expect(page.getByTestId("repo-agents-list")).toBeVisible();
    },
  );

  test(
    "an agent expands by keyboard to its full plain-text prompt and launch setup, and the URL names it",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const engineKey = await registerEngine(request, e2eRoot);
      await openCatalogue(page, engineKey);

      const summary = page.getByTestId(`agent-row-${IMPLEMENTER_ID}-summary`);
      await summary.focus();
      await page.keyboard.press("Enter");

      await expect(page).toHaveURL(new RegExp(`[?&]agent=${encodeURIComponent(IMPLEMENTER_ID)}`));
      const prompt = page.getByTestId(`agent-${IMPLEMENTER_ID}-prompt-text`);
      await expect(prompt).toBeVisible();
      expect(await prompt.evaluate((el) => el.tagName)).toBe("PRE");
      expect(await prompt.evaluate((el) => el.querySelector("h1, h2, strong, em, ul"))).toBeNull();

      const setup = page.getByTestId(`agent-${IMPLEMENTER_ID}-setup`);
      await expect(setup.getByTestId(`agent-${IMPLEMENTER_ID}-setup-model`)).toBeVisible();
      await expect(setup.getByTestId(`agent-${IMPLEMENTER_ID}-setup-effort`)).toBeVisible();
      await expect(setup.getByTestId(`agent-${IMPLEMENTER_ID}-setup-tools_effective`)).toBeVisible();
      await expect(page.getByTestId(`agent-${IMPLEMENTER_ID}-user-message-text`)).toBeVisible();

      await page.keyboard.press("Enter");
      await expect(page).not.toHaveURL(/[?&]agent=/);
    },
  );

  test(
    "next and previous match walk the highlighted occurrences inside an expanded prompt",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const engineKey = await registerEngine(request, e2eRoot);
      await openCatalogue(
        page,
        engineKey,
        `?q=the&agent=${encodeURIComponent(IMPLEMENTER_ID)}`,
      );

      const prompt = page.getByTestId(`agent-${IMPLEMENTER_ID}-prompt`);
      const status = prompt.getByTestId(`agent-${IMPLEMENTER_ID}-prompt-status`);
      await expect(status).toContainText("Match 1 of");
      await prompt.getByTestId(`agent-${IMPLEMENTER_ID}-prompt-next`).focus();
      await page.keyboard.press("Enter");
      await expect(status).toContainText("Match 2 of");
      await prompt.getByTestId(`agent-${IMPLEMENTER_ID}-prompt-prev`).focus();
      await page.keyboard.press("Enter");
      await expect(status).toContainText("Match 1 of");
      await expect(prompt.locator("mark[data-current='true']")).toHaveCount(1);
    },
  );

  test(
    "filtering by kind narrows the list, a search with no match offers clear, and clear restores everything",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const engineKey = await registerEngine(request, e2eRoot);
      await openCatalogue(page, engineKey);
      const total = shownCount(await page.getByTestId("agent-search-count").textContent()).total;

      await page.getByTestId("agent-filter-kind").selectOption("chat");
      await expect(page).toHaveURL(/[?&]kind=chat/);
      await expect
        .poll(async () => shownCount(await page.getByTestId("agent-search-count").textContent()).shown)
        .toBeLessThan(total);

      await page.getByTestId("agent-search-input").fill("zzz-no-agent-has-this-text");
      await expect(page.getByTestId("repo-agents-no-match")).toBeVisible();
      await page.getByTestId("repo-agents-clear").click();

      await expect(page.getByTestId("repo-agents-list")).toBeVisible();
      await expect(page.getByTestId("agent-search-input")).toHaveValue("");
      await expect(page.getByTestId("agent-search-count")).toContainText(`Showing ${String(total)} of ${String(total)}`);
      await expect(page).toHaveURL(new RegExp(`/repos/${engineKey}/agents$`));
    },
  );

  test(
    "the legacy standalone planning endpoint is listed and flagged, not hidden",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const engineKey = await registerEngine(request, e2eRoot);
      await openCatalogue(page, engineKey, "?q=legacy_planning");

      await page.getByTestId(/^agent-row-.*-summary$/).first().click();
      await expect(page.getByTestId(/-flags-legacy_planning_endpoint$/).first()).toBeVisible();
    },
  );
});
