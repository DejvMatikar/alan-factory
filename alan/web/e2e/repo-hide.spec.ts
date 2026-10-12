import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { openCommandPalette } from "./fixtures/helpers";
import { repoRoot, serverEnv } from "./fixtures/server-env";
import { expect, test } from "./fixtures/test-base";
import { seedControlToken, seedProjectFeatures } from "./fixtures/seed";

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

async function createWorkspace(
  request: APIRequestContext,
  title: string,
  engineKey: string,
): Promise<string> {
  const res = await request.post("/api/workspaces", {
    data: { title, description: "repo-hide e2e", engine_key: engineKey },
  });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { task_id: string };
  return body.task_id;
}

test.beforeEach(async ({ page, e2eRoot }) => {
  await seedProjectFeatures(page, true);
  await seedControlToken(page, e2eRoot);
});

interface HideScopingFinding {
  findingId: string;
  slug: string;
}

function seedHideScopingHealthState(e2eRoot: string, findings: HideScopingFinding[]): void {
  const surfaces = findings
    .map(
      ({ findingId, slug }) => `
    ClassifiedSurface(
        surface_id=${JSON.stringify(findingId)},
        kind="credential_expiry",
        is_blocked=True,
        blocker_reason="credential_expiry",
        obs={"engine_key": ${JSON.stringify(slug)}, "name": "e2e-hide-scoping", "expires_at": "2099-01-01"},
    ),`,
    )
    .join("");
  const script = `
import os
from datetime import UTC, datetime
from pathlib import Path

from alan.engine_config import load_persisted_engines
from alan.health import compute_health_state, save_health_state
from alan.queue import Queue
from alan.teams.triage_pregate import ClassifiedSurface

load_persisted_engines()
queue = Queue(db_path=Path(os.environ["ALAN_QUEUE_DB_PATH"]))
surfaces = [${surfaces}
]
save_health_state(queue, compute_health_state(surfaces, datetime.now(UTC)))
`;
  execFileSync("uv", ["run", "python", "-c", script], {
    cwd: repoRoot,
    env: { ...process.env, ...serverEnv(e2eRoot, "e2e-hide-scoping-seed") },
  });
}

async function healthFindingIds(request: APIRequestContext): Promise<Set<string>> {
  const res = await request.get("/api/health");
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as {
    health_state: { findings: { finding_id: string }[] } | null;
  };
  return new Set((body.health_state?.findings ?? []).map((f) => f.finding_id));
}

test(
  "hiding a repository removes it from its own page, from a scope-cleared /build fallback, and from the command palette, while a sibling repo and the default repo stay reachable on their own pages",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const hiddenOwner = `e2e-hide-owner-${suffix}`;
    const hiddenRepo = `hide-me-${suffix}`;
    const hiddenSlug = `${hiddenOwner}/${hiddenRepo}`;
    const keptOwner = `e2e-keep-owner-${suffix}`;
    const keptRepo = `keep-me-${suffix}`;
    const keptSlug = `${keptOwner}/${keptRepo}`;

    await registerEngine(request, token, hiddenOwner, hiddenRepo);
    await registerEngine(request, token, keptOwner, keptRepo);

    const beforeHide = await request.get("/api/engines");
    const baselineHiddenCount = (
      (await beforeHide.json()) as { engines: { hidden?: boolean }[] }
    ).engines.filter((e) => e.hidden === true).length;

    const hiddenFindingId = `e2e-hide-scoping-hidden-${suffix}`;
    const keptFindingId = `e2e-hide-scoping-kept-${suffix}`;
    const hideScopingFindings: HideScopingFinding[] = [
      { findingId: hiddenFindingId, slug: hiddenSlug },
      { findingId: keptFindingId, slug: keptSlug },
    ];

    seedHideScopingHealthState(e2eRoot, hideScopingFindings);
    const beforeHideFindingIds = await healthFindingIds(request);
    expect(beforeHideFindingIds.has(hiddenFindingId)).toBe(true);
    expect(beforeHideFindingIds.has(keptFindingId)).toBe(true);

    await page.goto(`/repos/${hiddenSlug}`);
    await expect(page.getByTestId(`repo-section-${hiddenSlug}`)).toBeVisible();
    await page.goto(`/repos/${keptSlug}`);
    await expect(page.getByTestId(`repo-section-${keptSlug}`)).toBeVisible();

    await page.goto(`/repos/${hiddenSlug}`);
    await page.getByTestId(`repo-action-hide-${hiddenSlug}`).click();
    const hideAnnouncement = page.getByTestId("hidden-repo-announcement");
    await expect(hideAnnouncement).toBeVisible();
    await expect(hideAnnouncement).not.toHaveClass(/sr-only/);
    await expect(page).toHaveURL((u) => new URL(u).pathname !== `/repos/${hiddenSlug}`);

    seedHideScopingHealthState(e2eRoot, hideScopingFindings);
    const afterHideFindingIds = await healthFindingIds(request);
    expect(afterHideFindingIds.has(hiddenFindingId)).toBe(false);
    expect(afterHideFindingIds.has(keptFindingId)).toBe(true);

    await expect(page.getByTestId(`top-bar-repo-${hiddenSlug}`)).toHaveCount(0);
    await expect(page.getByTestId(`top-bar-repo-${keptSlug}`)).toBeVisible();
    await expect(page.getByTestId(`top-bar-repo-${"DejvMatikar/alan"}`)).toBeVisible();

    await expect(page.getByTestId("hidden-repo-dialog-trigger")).toContainText(
      `Hidden (${String(baselineHiddenCount + 1)})`,
    );
    await page.getByTestId("hidden-repo-dialog-trigger").click();
    await expect(page.getByTestId(`hidden-repo-row-${hiddenSlug}`)).toBeVisible();
    await page.keyboard.press("Escape");

    await page.evaluate(() => sessionStorage.clear());
    await page.goto("/build");
    await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL((u) => new URL(u).pathname === "/repos/DejvMatikar/alan/build");
    await expect(page).not.toHaveURL(new RegExp(hiddenSlug.replace("/", "\\/")));

    await page.goto(`/repos/${keptSlug}`);
    await openCommandPalette(page, "Control+Alt+B");
    await expect(page.getByRole("heading", { name: "Build" })).toBeVisible();
    await expect(page.getByTestId(`palette-repo-${hiddenSlug}`)).toHaveCount(0);
    await expect(page.getByTestId(`palette-repo-${keptSlug}`)).toBeVisible();
  },
);

test(
  "the hide outcome reports honestly, and the hidden repository stays reachable through a direct link",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-hide-report-owner-${suffix}`;
    const repo = `hide-report-${suffix}`;
    const slug = `${owner}/${repo}`;

    await registerEngine(request, token, owner, repo);

    await page.goto(`/repos/${slug}`);
    await page.getByTestId(`repo-action-hide-${slug}`).click();
    const notice = page.getByTestId("hidden-repo-announcement");
    await expect(notice).toBeVisible();
    await expect(notice).not.toHaveClass(/sr-only/);
    await expect(notice).toContainText("stopped cleanly");

    await page.goto(`/repos/${owner}/${repo}/config`);
    await expect(page.getByTestId("repo-engine-config-identity")).toContainText(slug);
    await expect(page.getByTestId("repo-engine-config-hidden-notice")).toBeVisible();
  },
);

test(
  "restoring a hidden repository from the dialog brings its top-bar entry, prior autonomy, and workspaces back with no page reload",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const suffix = Date.now();
    const owner = `e2e-restore-owner-${suffix}`;
    const repo = `restore-me-${suffix}`;
    const slug = `${owner}/${repo}`;

    await registerEngine(request, token, owner, repo);
    await setPosture(request, token, owner, repo, "autonomous");
    const taskId = await createWorkspace(request, `E2E restore workspace ${suffix}`, slug);

    await page.goto(`/repos/${slug}`);
    await expect(page.getByTestId(`repo-section-${slug}`)).toBeVisible();
    await expect(page.getByTestId(`repo-action-maintenance-${slug}`)).toHaveJSProperty(
      "tagName",
      "BUTTON",
    );
    await expect(page.getByTestId(`repo-action-maintenance-${slug}`)).toBeEnabled();
    await expect(page.getByTestId(`home-row-feature-${taskId}`)).toBeVisible();

    await page.getByTestId(`repo-action-hide-${slug}`).click();
    const hideAnnouncement = page.getByTestId("hidden-repo-announcement");
    await expect(hideAnnouncement).toBeVisible();
    await expect(hideAnnouncement).not.toHaveClass(/sr-only/);
    await expect(page).toHaveURL((u) => new URL(u).pathname !== `/repos/${slug}`);

    await page.getByTestId("hidden-repo-dialog-trigger").click();
    await page.getByTestId(`hidden-repo-restore-${slug}`).click();
    await expect(page.getByTestId("hidden-repo-announcement")).toContainText(`${slug} restored.`);
    await expect(page.getByTestId(`hidden-repo-row-${slug}`)).toHaveCount(0);
    await expect(page.getByTestId(`top-bar-repo-${slug}`)).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByTestId(`top-bar-repo-${slug}`).click();
    await expect(page.getByTestId(`repo-section-${slug}`)).toBeVisible();
    await expect(page.getByTestId(`repo-action-maintenance-${slug}`)).toHaveJSProperty(
      "tagName",
      "BUTTON",
    );
    await expect(page.getByTestId(`repo-action-maintenance-${slug}`)).toBeEnabled();
    await expect(page.getByTestId(`home-row-feature-${taskId}`)).toBeVisible();
  },
);
