import fs from "node:fs";
import path from "node:path";

import {
  expect,
  type APIRequestContext,
  type Locator,
  type Page,
} from "@playwright/test";

export function ensureHangFile(hangFile: string, marker: string): void {
  fs.mkdirSync(path.dirname(hangFile), { recursive: true });
  fs.writeFileSync(hangFile, marker);
}

export function clearHangFile(hangFile: string): void {
  try {
    fs.unlinkSync(hangFile);
  } catch {
  }
}

async function pressUntil(
  page: Page,
  shortcut: string,
  launched: () => Promise<boolean> | boolean,
): Promise<void> {
  await expect
    .poll(
      async () => {
        if (await launched()) return true;
        await page.keyboard.press(shortcut);
        return false;
      },
      { timeout: 15_000, intervals: [200, 300, 500, 1_000] },
    )
    .toBe(true);
}

async function launchedOrDismissPalette(
  page: Page,
  launched: () => Promise<boolean> | boolean,
): Promise<boolean> {
  if (await launched()) return true;
  const palette = page.getByTestId("command-palette");
  if (await palette.isVisible().catch(() => false)) await page.keyboard.press("Escape");
  return false;
}

export async function openExploreFromHome(page: Page): Promise<void> {
  const modal = page.getByTestId("consultant-modal");
  await pressUntil(page, "Control+Shift+E", () =>
    launchedOrDismissPalette(page, () => modal.isVisible().catch(() => false)),
  );
}

export async function startBuildFromHome(page: Page): Promise<void> {
  await pressUntil(page, "Control+Shift+X", () =>
    launchedOrDismissPalette(page, () => new URL(page.url()).pathname.endsWith("/build")),
  );
}

export async function openCommandPalette(page: Page, shortcut: string): Promise<void> {
  const palette = page.getByTestId("command-palette");
  await pressUntil(page, shortcut, () => palette.isVisible().catch(() => false));
}

const CHAT_SESSION_URL_RE = /[?&]s=[0-9a-f-]{36}/;

export async function settleChatSession(page: Page): Promise<void> {
  const firstChooserOption = page
    .getByTestId("repo-chooser-modal")
    .locator('[data-testid^="repo-option-"]')
    .first();
  await expect
    .poll(
      async () => {
        if (CHAT_SESSION_URL_RE.test(page.url())) return true;
        if (await firstChooserOption.isVisible().catch(() => false)) {
          await firstChooserOption.click({ timeout: 1_000 }).catch(() => undefined);
        }
        return false;
      },
      { timeout: 30_000, intervals: [200, 300, 500, 1_000] },
    )
    .toBe(true);
}

export async function gotoBuildSession(page: Page): Promise<void> {
  await page.goto("/build");
  await settleChatSession(page);
}

interface WorkspaceSummary {
  task_id: string;
  status: string;
  latest_invocation_status: string | null;
}

function isBusy(w: WorkspaceSummary): boolean {
  return (
    (w.latest_invocation_status === "running" ||
      w.latest_invocation_status === "pending") &&
    w.status === "active"
  );
}

export async function waitForDispatcherIdle(
  request: APIRequestContext,
  { timeout = 30_000 }: { timeout?: number } = {},
): Promise<void> {
  await expect
    .poll(
      async () => {
        const res = await request.get("/api/workspaces");
        if (!res.ok()) return `unreachable: GET /api/workspaces -> ${res.status()}`;
        const all = (await res.json()) as WorkspaceSummary[];
        const busy = all.filter(isBusy);
        if (busy.length > 0) {
          return `busy: ${busy
            .map((w) => `${w.task_id}(${w.status}/${w.latest_invocation_status})`)
            .join(", ")}`;
        }
        return "idle";
      },
      { timeout, intervals: [200, 500, 1000] },
    )
    .toBe("idle");
}

export async function waitForGraphNode(
  page: Page,
  testId: string,
): Promise<Locator> {
  const graphTab = page.getByRole("tab", { name: "Graph" }).first();
  const graphTabAppeared = await graphTab
    .waitFor({ state: "visible", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  if (graphTabAppeared) {
    await graphTab.click();
  }
  await expect(page.getByTestId("invocation-graph")).toBeVisible();
  const node = page.getByTestId(testId);
  await expect(node).toBeVisible({ timeout: 30_000 });
  return node;
}
