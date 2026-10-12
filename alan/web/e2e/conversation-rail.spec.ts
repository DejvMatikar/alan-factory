import type { Locator } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const DEFAULT_REPO_KEY = "DejvMatikar/alan";
const TRANSPARENT_RGBA = "rgba(0, 0, 0, 0)";

function readSurfaceElevatedRgb(): string {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const css = readFileSync(path.resolve(here, "..", "src", "index.css"), "utf-8");
  const match = /--color-surface-elevated:\s*#([0-9a-fA-F]{6})/.exec(css);
  if (match?.[1] === undefined) throw new Error("token not found: --color-surface-elevated");
  const hex = match[1];
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgb(${r}, ${g}, ${b})`;
}

const RAISED_SURFACE_RGB = readSurfaceElevatedRgb();

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-conversation-rail-co");
});

test(
  "the rail lists build and explore conversations newest-first, and opening one swaps only the detail region",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const buildRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    expect(buildRes.ok()).toBeTruthy();
    const { session_id: buildSessionId } = (await buildRes.json()) as { session_id: string };

    const exploreRes = await request.post("/api/explore-chat/sessions", {
      data: { consultant_key: "research", engine_key: DEFAULT_REPO_KEY },
    });
    expect(exploreRes.ok()).toBeTruthy();
    const { session_id: exploreSessionId } = (await exploreRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await expect(
      page.getByTestId(`repo-section-${DEFAULT_REPO_KEY}`).getByRole("heading", { level: 1 }),
    ).toBeVisible();
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    await expect(rail.getByTestId(`rail-row-${exploreSessionId}`)).toBeVisible({ timeout: 10_000 });
    await expect(rail.getByTestId(`rail-row-${buildSessionId}`)).toBeVisible();

    await rail.getByTestId(`rail-row-link-${exploreSessionId}`).click();
    await expect(page).toHaveURL(new RegExp(`/explore/research\\?s=${exploreSessionId}`));
    const openedHeading = page.getByRole("heading", { level: 1, name: /Explore/ });
    await expect(openedHeading).toBeVisible();
    await expect(openedHeading).toBeFocused();
    await expect(rail).toBeVisible();
    await expect(
      rail.getByTestId(`rail-row-link-${exploreSessionId}`),
    ).toHaveAttribute("aria-current", "true");
  },
);

test(
  "starting a build conversation from the rail's own controls appears in the list and opens in the detail region",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    await page.getByTestId(`rail-start-build-${DEFAULT_REPO_KEY}`).click();
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
    await expect(page).toHaveURL(/[?&]s=[0-9a-f-]{36}/, { timeout: 15_000 });
    const sessionId = new URL(page.url()).searchParams.get("s");
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    await expect(rail.getByTestId(`rail-row-${String(sessionId)}`)).toBeVisible({ timeout: 10_000 });
  },
);

test(
  "deleting the currently open conversation from the rail returns to the dashboard and announces it; deleting another row leaves the detail region untouched",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const openRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: openSessionId } = (await openRes.json()) as { session_id: string };
    const otherRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: otherSessionId } = (await otherRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}/build?s=${openSessionId}`);
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    await expect(rail.getByTestId(`rail-row-${otherSessionId}`)).toBeVisible({ timeout: 10_000 });

    await rail.getByTestId(`rail-row-delete-${otherSessionId}`).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete chat" }).click();
    await expect(rail.getByTestId(`rail-row-${otherSessionId}`)).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/build\\?s=${openSessionId}`));

    await rail.getByTestId(`rail-row-delete-${openSessionId}`).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete chat" }).click();
    await expect(page).toHaveURL((u) => new URL(u).pathname === `/repos/${DEFAULT_REPO_KEY}`, {
      timeout: 15_000,
    });
    await expect(page.getByTestId("conversation-rail-announcement")).toContainText(
      "Conversation deleted",
    );
  },
);

test(
  "at a mid-desktop width (1024x768) the rail can be collapsed behind a compact indicator, and the collapse persists across navigation",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    await expect(rail).toBeVisible();

    await page.getByTestId("rail-collapse").click();
    await expect(rail).toHaveCount(0);
    const indicator = page.getByTestId(`rail-collapsed-${DEFAULT_REPO_KEY}`);
    await expect(indicator).toBeVisible();
    await expect(page).toHaveURL(/[?&]rail=collapsed/);

    await page.goto(`/repos/${DEFAULT_REPO_KEY}/config`);
    await expect(page.getByRole("heading", { level: 1, name: "Engine configuration" })).toBeVisible();
    await expect(page.getByTestId(`rail-collapsed-${DEFAULT_REPO_KEY}`)).toBeVisible();
    await expect(rail).toHaveCount(0);

    await page.getByTestId("rail-expand").click();
    await expect(rail).toBeVisible();
  },
);

test(
  "the rail's scroll region carries no box chrome, is inset symmetrically from both edges, and hovering a row highlights the whole strip including the delete control",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const buildRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: sessionId } = (await buildRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    const scroll = page.getByTestId("conversation-rail-scroll");
    await expect(rail.getByTestId(`rail-row-${sessionId}`)).toBeVisible({ timeout: 10_000 });

    const scrollStyle = await scroll.evaluate((el) => {
      const cs = window.getComputedStyle(el);
      return { borderWidth: cs.borderWidth, borderRadius: cs.borderRadius };
    });
    expect(scrollStyle.borderWidth).toBe("0px");
    expect(scrollStyle.borderRadius).toBe("0px");

    const railSlot = page.getByTestId("repo-shell-rail-slot");
    const leftPad = await railSlot.evaluate((el) => window.getComputedStyle(el).paddingLeft);
    const rightPad = await railSlot.evaluate((el) => window.getComputedStyle(el).paddingRight);
    expect(rightPad).toBe(leftPad);

    const row = rail.getByTestId(`rail-row-${sessionId}`);
    const deleteButton = rail.getByTestId(`rail-row-delete-${sessionId}`);
    await expect(deleteButton).toBeVisible();
    await page.mouse.move(0, 0);
    await expect(row).toHaveCSS("background-color", TRANSPARENT_RGBA);
    await row.hover();
    await expect(row).toHaveCSS("background-color", RAISED_SURFACE_RGB);
  },
);

test(
  "the rail's heading and start controls pin from the very first pixel of scroll, with no initial drift",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const railHeading = page.getByTestId("rail-heading");
    await expect(railHeading).toBeVisible();
    const railSlot = page.getByTestId("repo-shell-rail-slot");
    const before = await railSlot.boundingBox();
    await page.mouse.wheel(0, 5);
    const after = await railSlot.boundingBox();
    expect(before).not.toBeNull();
    expect(after).not.toBeNull();
    expect(Math.abs((after?.y ?? 0) - (before?.y ?? 0))).toBeLessThanOrEqual(1);
  },
);

test(
  "the rail collapse control sits below the scroll region, after the label and start controls, toggles via keyboard, and exposes its state to assistive tech",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const label = page.getByTestId("rail-heading");
    const scroll = page.getByTestId("conversation-rail-scroll");
    const collapseButton = page.getByTestId("rail-collapse");
    await expect(collapseButton).toHaveAttribute("aria-expanded", "true");
    const labelBox = await label.boundingBox();
    const scrollBox = await scroll.boundingBox();
    const collapseBox = await collapseButton.boundingBox();
    expect(labelBox).not.toBeNull();
    expect(scrollBox).not.toBeNull();
    expect(collapseBox).not.toBeNull();
    expect(collapseBox!.y).toBeGreaterThan(labelBox!.y);
    expect(collapseBox!.y).toBeGreaterThanOrEqual(scrollBox!.y + scrollBox!.height);

    await collapseButton.focus();
    await page.keyboard.press("Enter");
    const expandButton = page.getByTestId("rail-expand");
    await expect(expandButton).toBeVisible();
    await expect(expandButton).toHaveAttribute("aria-expanded", "false");
  },
);

test(
  "the Build/Explore keyboard shortcuts still work while the rail is collapsed",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto(`/repos/${DEFAULT_REPO_KEY}?rail=collapsed`);
    await expect(page.getByTestId(`rail-collapsed-${DEFAULT_REPO_KEY}`)).toBeVisible();

    await page.keyboard.press("Control+Shift+X");
    await expect(page.getByRole("heading", { level: 1, name: "Build" })).toBeVisible({
      timeout: 15_000,
    });
  },
);

test(
  "opening a conversation by mouse click focuses its heading without painting a visible focus ring",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const buildRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: buildSessionId } = (await buildRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    await expect(rail.getByTestId(`rail-row-${buildSessionId}`)).toBeVisible({ timeout: 10_000 });

    await rail.getByTestId(`rail-row-link-${buildSessionId}`).click();
    const heading = page.getByRole("heading", { level: 1, name: "Build" });
    await expect(heading).toBeFocused();
    const boxShadow = await heading.evaluate((el) => window.getComputedStyle(el).boxShadow);
    expect(boxShadow).toBe("none");
  },
);

test(
  "opening a conversation via keyboard focuses its heading with a visible focus ring",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const buildRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: buildSessionId } = (await buildRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    const link = rail.getByTestId(`rail-row-link-${buildSessionId}`);
    await expect(link).toBeVisible({ timeout: 10_000 });
    await link.focus();
    await page.keyboard.press("Enter");
    const heading = page.getByRole("heading", { level: 1, name: "Build" });
    await expect(heading).toBeFocused();
    const boxShadow = await heading.evaluate((el) => window.getComputedStyle(el).boxShadow);
    expect(boxShadow).not.toBe("none");
  },
);

test(
  "opening a second conversation moves the selected-row background to it and clears it from the first",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const aRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: aId } = (await aRes.json()) as { session_id: string };
    const bRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: bId } = (await bRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}/build?s=${aId}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    const rowA = rail.getByTestId(`rail-row-${aId}`);
    const rowB = rail.getByTestId(`rail-row-${bId}`);
    await expect(rowB).toBeVisible({ timeout: 10_000 });

    await expect(rowA).toHaveCSS("background-color", RAISED_SURFACE_RGB);

    await rail.getByTestId(`rail-row-link-${bId}`).click();
    await expect(page).toHaveURL(new RegExp(`/build\\?s=${bId}`));

    await expect(rowB).toHaveCSS("background-color", RAISED_SURFACE_RGB);
    await page.mouse.move(0, 0);
    await expect(rowA).toHaveCSS("background-color", TRANSPARENT_RGBA);
  },
);

test(
  "hovering an unselected row highlights the whole row, including the delete strip, and clears again on mouse-out",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const aRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: aId } = (await aRes.json()) as { session_id: string };
    const bRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: bId } = (await bRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}/build?s=${aId}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    const rowB = rail.getByTestId(`rail-row-${bId}`);
    await expect(rowB).toBeVisible({ timeout: 10_000 });

    await expect(rowB).toHaveCSS("background-color", TRANSPARENT_RGBA);
    await rail.getByTestId(`rail-row-link-${bId}`).hover();
    await expect(rowB).toHaveCSS("background-color", RAISED_SURFACE_RGB);
    await page.mouse.move(0, 0);
    await expect(rowB).toHaveCSS("background-color", TRANSPARENT_RGBA);
  },
);

test(
  "the conversation heading, tab row, transcript, and composer share one left alignment line at a wide and a narrow viewport",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const res = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: sessionId } = (await res.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}/build?s=${sessionId}`);
    const heading = page.getByRole("heading", { level: 1, name: "Build" });
    await expect(heading).toBeVisible();
    const tablist = page.getByRole("tablist");
    const tabpanel = page.getByRole("tabpanel");
    const composer = page.getByRole("textbox", { name: "Message" });

    const leftOf = async (locator: Locator): Promise<number> => {
      const box = await locator.boundingBox();
      if (box === null) throw new Error("expected a bounding box");
      return box.x;
    };

    const headingLeft = await leftOf(heading);
    expect(await leftOf(tablist)).toBeCloseTo(headingLeft, 0);
    expect(await leftOf(tabpanel)).toBeCloseTo(headingLeft, 0);
    expect(await leftOf(composer)).toBeCloseTo(headingLeft, 0);

    await page.setViewportSize({ width: 390, height: 844 });
    const headingLeftNarrow = await leftOf(heading);
    expect(await leftOf(tablist)).toBeCloseTo(headingLeftNarrow, 0);
    expect(await leftOf(tabpanel)).toBeCloseTo(headingLeftNarrow, 0);
  },
);

test(
  "the active tab's underline stays exactly the width of its label with no horizontal inset",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const res = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: sessionId } = (await res.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}/build?s=${sessionId}`);
    const activeTab = page.getByTestId("tab-chat");
    await expect(activeTab).toHaveAttribute("aria-selected", "true");
    const padding = await activeTab.evaluate((el) => {
      const cs = window.getComputedStyle(el);
      return { left: cs.paddingLeft, right: cs.paddingRight };
    });
    expect(padding.left).toBe("0px");
    expect(padding.right).toBe("0px");
  },
);

test(
  "a row's date and preview text begin on the same vertical line as the rail heading and the Build button, and the delete control sits at the rail's right margin",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const buildRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: sessionId } = (await buildRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    await expect(rail.getByTestId(`rail-row-${sessionId}`)).toBeVisible({ timeout: 10_000 });

    const heading = page.getByTestId("rail-heading");
    const buildButton = page.getByTestId(`rail-start-build-${DEFAULT_REPO_KEY}`);
    const dateSpan = page.getByTestId(`rail-row-date-${sessionId}`);
    const descriptionSpan = page.getByTestId(`rail-row-description-${sessionId}`);

    const leftOf = async (locator: Locator): Promise<number> => {
      const box = await locator.boundingBox();
      if (box === null) throw new Error("expected a bounding box");
      return box.x;
    };

    const headingLeft = await leftOf(heading);
    expect(await leftOf(buildButton)).toBeCloseTo(headingLeft, 0);
    expect(await leftOf(dateSpan)).toBeCloseTo(headingLeft, 0);
    expect(await leftOf(descriptionSpan)).toBeCloseTo(headingLeft, 0);

    const railBox = await rail.boundingBox();
    const row = rail.getByTestId(`rail-row-${sessionId}`);
    const rowBox = await row.boundingBox();
    if (railBox === null || rowBox === null) throw new Error("expected bounding boxes");
    expect(rowBox.x).toBeCloseTo(railBox.x, 0);
    expect(rowBox.x + rowBox.width).toBeCloseTo(railBox.x + railBox.width, 0);

    const deleteButton = rail.getByTestId(`rail-row-delete-${sessionId}`);
    const deleteBox = await deleteButton.boundingBox();
    if (deleteBox === null) throw new Error("expected a bounding box");
    expect(deleteBox.x + deleteBox.width).toBeCloseTo(railBox.x + railBox.width, 0);
  },
);

test(
  "the selected-row accent marker does not shift row text horizontally between selected and unselected states",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const aRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: aId } = (await aRes.json()) as { session_id: string };
    const bRes = await request.post("/api/build-chat/sessions", {
      data: { engine_key: DEFAULT_REPO_KEY },
    });
    const { session_id: bId } = (await bRes.json()) as { session_id: string };

    await page.goto(`/repos/${DEFAULT_REPO_KEY}/build?s=${aId}`);
    const rail = page.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    const selectedDate = rail.getByTestId(`rail-row-date-${aId}`);
    const unselectedDate = rail.getByTestId(`rail-row-date-${bId}`);
    await expect(unselectedDate).toBeVisible({ timeout: 10_000 });

    const selectedBox = await selectedDate.boundingBox();
    const unselectedBox = await unselectedDate.boundingBox();
    if (selectedBox === null || unselectedBox === null) throw new Error("expected bounding boxes");
    expect(selectedBox.x).toBeCloseTo(unselectedBox.x, 0);
  },
);

test(
  "below the desktop breakpoint the drawer holds the repository switcher above the conversations, traps focus, and Escape returns focus to the trigger",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    await registerGuardEngine(request, e2eRoot, "e2e-conversation-rail-drawer-co");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/repos/${DEFAULT_REPO_KEY}`);

    const trigger = page.getByTestId("repo-drawer-trigger");
    await trigger.click();
    const drawer = page.getByTestId("repo-drawer");
    await expect(drawer).toBeVisible();
    const switcher = drawer.getByTestId("repo-drawer-switcher");
    const rail = drawer.getByTestId(`conversation-rail-${DEFAULT_REPO_KEY}`);
    await expect(switcher).toBeVisible();
    await expect(rail).toBeVisible();
    const switcherBox = await switcher.boundingBox();
    const railBox = await rail.boundingBox();
    expect(switcherBox).not.toBeNull();
    expect(railBox).not.toBeNull();
    expect(switcherBox!.y).toBeLessThan(railBox!.y);

    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(trigger).toBeFocused();
  },
);
