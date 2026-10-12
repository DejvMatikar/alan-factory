import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext, Locator, Page } from "@playwright/test";

import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const OWNER = "e2e-refetch-stability-co";
const REPO = "widget";
const ENGINE_KEY = `${OWNER}/${REPO}`;
const DEFAULT_ENGINE_KEY = "DejvMatikar/alan";

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

async function waitForSettledBox(locator: Locator): Promise<Box> {
  let previousY: number | null = null;
  await expect
    .poll(async () => {
      const box = await locator.boundingBox();
      const settled = box !== null && previousY === box.y;
      previousY = box === null ? null : box.y;
      return settled;
    })
    .toBe(true);
  const box = await locator.boundingBox();
  if (box === null) {
    throw new Error("expected a bounding box after the layout settled");
  }
  return box;
}

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

async function setPosture(
  request: APIRequestContext,
  token: string,
  owner: string,
  repo: string,
  posture: string,
): Promise<void> {
  const res = await request.post(`/api/engines/${owner}/${repo}/posture`, {
    data: { posture, confirm: true },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(200);
}

async function createWorkspace(request: APIRequestContext, title: string, engineKey: string): Promise<void> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "home-refetch-stability e2e", engine_key: engineKey },
  });
  expect(res.ok()).toBeTruthy();
}

async function delaySecondRequest(page: Page, urlGlob: string, delayMs: number): Promise<void> {
  let seen = false;
  await page.route(urlGlob, async (route) => {
    if (!seen) {
      seen = true;
      await route.continue();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    await route.continue();
  });
}

interface EngineListEntry {
  readonly repo: { readonly owner: string; readonly repo: string };
}

async function scopeEngineListTo(page: Page, engineKey: string): Promise<void> {
  const kept = new Set([engineKey, DEFAULT_ENGINE_KEY]);
  await page.route("**/api/engines", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    const body = (await response.json()) as { engines: EngineListEntry[] };
    const engines = body.engines.filter((engine) => kept.has(`${engine.repo.owner}/${engine.repo.repo}`));
    await route.fulfill({ response, json: { ...body, engines } });
  });
}

test.describe("engines-bucket refetch stability", { tag: "@no-claude" }, () => {
  test(
    "an engines SSE event refreshes the repo status strip in place, including its recurring summary row, with no loading collapse and no layout jump",
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      await registerGuardEngine(request, e2eRoot, OWNER, REPO);
      const stamp = String(Date.now());
      for (let i = 0; i < 6; i += 1) {
        await createWorkspace(request, `e2e-refetch-stability-ws-${stamp}-${String(i)}`, ENGINE_KEY);
      }

      await delaySecondRequest(page, `**/api/engines/${OWNER}/${REPO}/status`, 600);
      await delaySecondRequest(page, `**/api/engines/${OWNER}/${REPO}/recurring-status`, 600);
      await scopeEngineListTo(page, ENGINE_KEY);
      const connectStatesRead = page.waitForResponse(
        (response) => new URL(response.url()).pathname === "/api/secrets" && response.request().method() === "GET",
      );

      await page.goto(`/repos/${ENGINE_KEY}`);
      const strip = page.getByTestId(`repo-status-strip-${ENGINE_KEY}`);
      const recurringTotal = page.getByTestId(`repo-status-group-${ENGINE_KEY}-recurring-total`);
      const resumeLines = page.locator('[data-testid^="needs-you-resume-"]');
      await expect(strip).toBeVisible();
      await expect(recurringTotal).toBeVisible();
      await expect(strip).toHaveAttribute("aria-busy", "false");
      await connectStatesRead;
      await expect(resumeLines).toHaveCount(0);

      await page.evaluate(() => window.scrollTo(0, 200));
      const stripBoxBefore = await waitForSettledBox(strip);
      const recurringBoxBefore = await waitForSettledBox(recurringTotal);
      const scrollBefore = await page.evaluate(() => window.scrollY);

      await setPosture(request, token, OWNER, REPO, "propose");

      await expect(strip).toHaveAttribute("aria-busy", "true", { timeout: 5_000 });
      await expect(page.getByTestId(`repo-status-loading-${ENGINE_KEY}`)).toHaveCount(0);
      await expect(strip).toBeVisible();
      await expect(recurringTotal).toBeVisible();

      const stripBoxDuring = await strip.boundingBox();
      const recurringBoxDuring = await recurringTotal.boundingBox();
      const scrollDuring = await page.evaluate(() => window.scrollY);
      if (stripBoxDuring === null || recurringBoxDuring === null) {
        throw new Error("expected the strip and recurring summary to keep a bounding box while refreshing");
      }
      expect(Math.abs(stripBoxDuring.y - stripBoxBefore.y)).toBeLessThan(2);
      expect(Math.abs(recurringBoxDuring.y - recurringBoxBefore.y)).toBeLessThan(2);
      expect(scrollDuring).toBe(scrollBefore);

      await expect(strip).toHaveAttribute("aria-busy", "false", { timeout: 5_000 });
      await expect(resumeLines).toHaveCount(0);
    },
  );

  test(
    "an engines SSE event during an open posture-confirm modal refreshes the tile in place without unmounting the dialog or mutating its snapshotted posture",
    async ({ page, request, e2eRoot }) => {
      const token = readControlToken(e2eRoot);
      await registerGuardEngine(request, e2eRoot, `${OWNER}-modal`, REPO);
      const owner = `${OWNER}-modal`;

      await page.goto(`/repos/${owner}/${REPO}/config`);
      const pill = page.getByTestId("engine-ops-posture-pill");
      await expect(pill).toHaveText("Observe");

      await page.getByTestId("engine-ops-posture-change-button").click();
      const dialog = page.getByTestId("posture-confirm-dialog");
      await expect(dialog).toBeVisible();
      const select = page.getByTestId("posture-confirm-select");
      await expect(select).toHaveValue("observe");
      const submit = page.getByTestId("posture-confirm-submit");
      await expect(submit).toBeDisabled();

      await setPosture(request, token, owner, REPO, "propose");
      await expect(pill).toHaveText("Propose", { timeout: 10_000 });

      await expect(dialog).toBeVisible();
      await expect(select).toHaveValue("observe");
      await expect(submit).toBeDisabled();
    },
  );

  test(
    "switching between the Active and Archived tabs changes only the table region — the status strip, its recurring summary, and the health control stay mounted with no refetch",
    async ({ page, request, e2eRoot }) => {
      await registerGuardEngine(request, e2eRoot, `${OWNER}-tabswitch`, REPO);
      const owner = `${OWNER}-tabswitch`;
      const engineKey = `${owner}/${REPO}`;
      await createWorkspace(request, `e2e-refetch-tabswitch-${String(Date.now())}`, engineKey);

      let statusCalls = 0;
      let recurringCalls = 0;
      await page.route(`**/api/engines/${owner}/${REPO}/status`, async (route) => {
        statusCalls += 1;
        await route.continue();
      });
      await page.route(`**/api/engines/${owner}/${REPO}/recurring-status`, async (route) => {
        recurringCalls += 1;
        await route.continue();
      });

      await page.goto(`/repos/${engineKey}`);
      const strip = page.getByTestId(`repo-status-strip-${engineKey}`);
      const recurringTotal = page.getByTestId(`repo-status-group-${engineKey}-recurring-total`);
      const health = page.getByTestId(`repo-health-${engineKey}`);
      await expect(strip).toBeVisible();
      await expect(recurringTotal).toBeVisible();
      await expect(health).toBeVisible();
      await expect.poll(() => statusCalls).toBeGreaterThan(0);
      await expect.poll(() => recurringCalls).toBeGreaterThan(0);
      const statusCallsBeforeSwitch = statusCalls;
      const recurringCallsBeforeSwitch = recurringCalls;

      await page.getByRole("tab", { name: "Archived" }).click();
      await expect(page.getByRole("tab", { name: "Archived" })).toHaveAttribute("aria-selected", "true");

      await expect(strip).toBeVisible();
      await expect(recurringTotal).toBeVisible();
      await expect(health).toBeVisible();
      expect(statusCalls).toBe(statusCallsBeforeSwitch);
      expect(recurringCalls).toBe(recurringCallsBeforeSwitch);
    },
  );
});
