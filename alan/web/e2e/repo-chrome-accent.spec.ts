import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext, Page } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";

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

async function accentEdgeOf(page: Page, testId: string): Promise<string> {
  return page.getByTestId(testId).evaluate((el) => el.style.getPropertyValue("--accent-edge"));
}

async function accentGlowOf(page: Page, testId: string): Promise<string> {
  return page.getByTestId(testId).evaluate((el) => el.style.getPropertyValue("--accent-glow"));
}

async function boxShadowOf(page: Page, testId: string): Promise<string> {
  return page.getByTestId(testId).evaluate((el) => getComputedStyle(el).boxShadow);
}

async function horizontalPaddingOf(
  page: Page,
  testId: string,
  side: "paddingLeft" | "paddingRight",
): Promise<string> {
  return page.getByTestId(testId).evaluate((el, key) => getComputedStyle(el)[key], side);
}

async function setAccent(
  request: APIRequestContext,
  token: string,
  owner: string,
  repo: string,
  accent: { color: string | null; intensity: string } | null,
): Promise<void> {
  const res = await request.put(`/api/engines/${owner}/${repo}/accent`, {
    data: { accent },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.ok()).toBeTruthy();
}

test(
  "a repository's accent stays the same across a reload",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-accent-stable-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, owner, repo);

    await page.goto(`/repos/${owner}/${repo}`);
    const testId = `top-bar-repo-${owner}/${repo}`;
    await expect(page.getByTestId(testId)).toBeVisible();
    const before = await accentEdgeOf(page, testId);
    expect(before).toMatch(/^#[0-9a-f]{6}$/i);

    await page.reload();
    await expect(page.getByTestId(testId)).toBeVisible();
    const after = await accentEdgeOf(page, testId);
    expect(after).toBe(before);
  },
);

test(
  "hiding a sibling repository never reshuffles another repository's accent",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const ownerA = `e2e-accent-stay-a-${suffix}`;
    const ownerB = `e2e-accent-stay-b-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, ownerA, repo);
    await registerEngine(request, token, ownerB, repo);

    await page.goto(`/repos/${ownerA}/${repo}`);
    const testIdA = `top-bar-repo-${ownerA}/${repo}`;
    await expect(page.getByTestId(testIdA)).toBeVisible();
    const beforeA = await accentEdgeOf(page, testIdA);

    const slugB = `${ownerB}/${repo}`;
    await page.goto(`/repos/${slugB}`);
    await page.getByTestId(`repo-action-hide-${slugB}`).click();
    const hideAnnouncement = page.getByTestId("hidden-repo-announcement");
    await expect(hideAnnouncement).toBeVisible();
    await expect(hideAnnouncement).not.toHaveClass(/sr-only/);

    await page.goto(`/repos/${ownerA}/${repo}`);
    await expect(page.getByTestId(testIdA)).toBeVisible();
    expect(await accentEdgeOf(page, testIdA)).toBe(beforeA);
  },
);

test(
  "the selected repository's name renders in its own accent colour; an unselected entry stays muted",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const ownerA = `e2e-accent-selected-a-${suffix}`;
    const ownerB = `e2e-accent-selected-b-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, ownerA, repo);
    await registerEngine(request, token, ownerB, repo);

    await page.goto(`/repos/${ownerA}/${repo}`);
    const selected = page.getByTestId(`top-bar-repo-${ownerA}/${repo}`);
    const unselected = page.getByTestId(`top-bar-repo-${ownerB}/${repo}`);
    await expect(selected).toHaveAttribute("aria-current", "true");
    await expect(unselected).not.toHaveAttribute("aria-current");
    await expect(selected).toHaveClass(/text-\[var\(--accent-fg\)\]/);
    await expect(unselected).toHaveClass(/text-text-muted/);
  },
);

test(
  "the accent frame paints two parallel lines — the rail's outer edge and the content column's outer edge — sharing one colour",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-accent-frame-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, owner, repo);

    await page.goto(`/repos/${owner}/${repo}`);
    const railSlot = page.getByTestId("repo-shell-rail-slot");
    const contentSlot = page.getByTestId("repo-shell-content-slot");
    await expect(railSlot).toBeVisible();
    await expect(contentSlot).toBeVisible();
    await expect
      .poll(() => accentEdgeOf(page, "repo-shell-rail-slot"))
      .toMatch(/^#[0-9a-f]{6}$/i);
    const railEdge = await accentEdgeOf(page, "repo-shell-rail-slot");
    const contentEdge = await accentEdgeOf(page, "repo-shell-content-slot");
    expect(railEdge).toMatch(/^#[0-9a-f]{6}$/i);
    expect(contentEdge).toBe(railEdge);
  },
);

test(
  "a newly-widened palette colour persists its accent edge across a reload",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-accent-widened-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, owner, repo);
    await setAccent(request, token, owner, repo, { color: "teal", intensity: "standard" });

    await page.goto(`/repos/${owner}/${repo}`);
    const testId = `top-bar-repo-${owner}/${repo}`;
    await expect(page.getByTestId(testId)).toBeVisible();
    const before = await accentEdgeOf(page, testId);
    expect(before).toMatch(/^#[0-9a-f]{6}$/i);

    await page.reload();
    await expect(page.getByTestId(testId)).toBeVisible();
    expect(await accentEdgeOf(page, testId)).toBe(before);
  },
);

test(
  "both frame lines carry a visible accent glow that follows intensity, absent for an explicit no-accent choice",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-accent-glow-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, owner, repo);
    await setAccent(request, token, owner, repo, { color: "orange", intensity: "soft" });

    await page.goto(`/repos/${owner}/${repo}`);
    const railSlot = page.getByTestId("repo-shell-rail-slot");
    await expect(railSlot).toBeVisible();

    await expect.poll(() => accentGlowOf(page, "repo-shell-rail-slot")).toMatch(/^#[0-9a-f]{8}$/i);
    const softGlow = await accentGlowOf(page, "repo-shell-rail-slot");
    expect(await accentGlowOf(page, "repo-shell-content-slot")).toBe(softGlow);
    const softAlpha = parseInt(softGlow.slice(7, 9), 16);
    expect(softAlpha).toBeGreaterThanOrEqual(0x66);
    expect(await boxShadowOf(page, "repo-shell-rail-slot")).not.toBe("none");
    expect(await boxShadowOf(page, "repo-shell-content-slot")).not.toBe("none");

    await setAccent(request, token, owner, repo, { color: "orange", intensity: "bold" });
    await page.reload();
    await expect.poll(() => accentGlowOf(page, "repo-shell-rail-slot")).not.toBe(softGlow);

    await setAccent(request, token, owner, repo, { color: null, intensity: "standard" });
    await page.reload();
    await expect(railSlot).toHaveClass(/border-border/);
    expect(await boxShadowOf(page, "repo-shell-rail-slot")).toBe("none");
    expect(await boxShadowOf(page, "repo-shell-content-slot")).toBe("none");
  },
);

test(
  "both frame lines carry consistent, symmetric spacing off their content at every breakpoint",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-accent-spacing-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, owner, repo);

    await page.goto(`/repos/${owner}/${repo}`);
    const railSlot = page.getByTestId("repo-shell-rail-slot");
    const contentSlot = page.getByTestId("repo-shell-content-slot");
    await expect(railSlot).toBeVisible();
    expect(await horizontalPaddingOf(page, "repo-shell-rail-slot", "paddingLeft")).toBe("16px");
    expect(await horizontalPaddingOf(page, "repo-shell-rail-slot", "paddingRight")).toBe("16px");
    expect(await horizontalPaddingOf(page, "repo-shell-content-slot", "paddingRight")).toBe("16px");
    const contentBottomPad = await contentSlot.evaluate((el) => getComputedStyle(el).paddingBottom);
    expect(contentBottomPad).toBe("16px");

    await page.getByTestId("rail-collapse").click();
    await expect(page.getByTestId(`rail-collapsed-${owner}/${repo}`)).toBeVisible();
    expect(await horizontalPaddingOf(page, "repo-shell-rail-slot", "paddingLeft")).toBe("16px");
    expect(await horizontalPaddingOf(page, "repo-shell-rail-slot", "paddingRight")).toBe("16px");

    await page.setViewportSize({ width: 375, height: 800 });
    await expect(contentSlot).toBeVisible();
    expect(await horizontalPaddingOf(page, "repo-shell-content-slot", "paddingRight")).toBe("16px");
  },
);

test(
  "an explicit no-accent choice repaints the frame in the neutral border token, and resetting to automatic restores a colour",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-accent-neutral-${suffix}`;
    const repo = "widget";
    await registerEngine(request, token, owner, repo);

    await setAccent(request, token, owner, repo, { color: null, intensity: "standard" });
    await page.goto(`/repos/${owner}/${repo}`);
    const railSlot = page.getByTestId("repo-shell-rail-slot");
    await expect(railSlot).toBeVisible();
    await expect(railSlot).toHaveClass(/border-border/);
    await expect(railSlot).not.toHaveClass(/border-\[var\(--accent-edge\)\]/);

    await setAccent(request, token, owner, repo, null);
    await page.reload();
    await expect(railSlot).toHaveClass(/border-\[var\(--accent-edge\)\]/);
    const edge = await accentEdgeOf(page, "repo-shell-rail-slot");
    expect(edge).toMatch(/^#[0-9a-f]{6}$/i);
  },
);

test(
  "every navigation control's description reveals on keyboard focus and dismisses with Escape",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health");
    const gear = page.getByTestId("settings-gear");
    await gear.focus();
    const description = page.getByText("App-wide preferences: control token, theme, and other global settings.");
    await expect(description).not.toHaveClass(/sr-only/);
    await page.keyboard.press("Escape");
    await expect(description).toHaveClass(/sr-only/);
    await expect(gear).toBeFocused();
  },
);

test(
  "a conversation's delete control renders as a red waste-bin icon, not a neutral cross",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const DEFAULT_REPO_KEY = "DejvMatikar/alan";
    const res = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    expect(res.ok()).toBeTruthy();
    const { session_id: sessionId } = (await res.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    const deleteButton = rail.getByTestId(`rail-row-delete-${sessionId}`);
    await expect(deleteButton).toBeVisible();
    await expect(deleteButton).toHaveAttribute("data-variant", "destroy");
    await expect(deleteButton.locator('[data-testid="icon-trash"]')).toBeVisible();
    await expect(deleteButton).not.toHaveText("×");
  },
);

test(
  "the repo dashboard's needs-you slot carries no cross-repository conversations panel",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/repos/DejvMatikar/alan");
    await expect(page.getByTestId("home-needs-you-slot")).toBeVisible();
    await expect(page.getByTestId("home-resume-drafts-slot")).toHaveCount(0);
    await expect(page.getByTestId("resume-drafts-panel")).toHaveCount(0);
  },
);
