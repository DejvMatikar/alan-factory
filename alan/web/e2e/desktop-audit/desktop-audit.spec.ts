import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  buildDesktopScreenReport,
  type DesktopAuditSpec,
  type DesktopAuditState,
  type DesktopScreenReport,
} from "../../src/desktop-audit/audit-helpers";
import { compareToBaseline } from "../../src/desktop-audit/baseline-compare";
import { extractGeometryTree } from "../../src/desktop-audit/geometry";
import { validateDesktopStateRegistry } from "../../src/desktop-audit/registry";
import { resolveDesktopAuditMaxRoot, resolveDesktopAuditStandardRoot } from "../../src/desktop-audit-config";
import { installedDefaultProfile, TEST_DEFAULT_PROFILE } from "../fixtures/default-profile";
import { seedBlockingQuestion, seedProjectFeatures } from "../fixtures/seed";
import { SCREENS, type AuditScreen } from "../mobile-audit/screens";
import { loadRatchetedDesktopBaseline } from "./load-baseline";
import { formatComparisonDiff } from "../../src/ui-audit/baseline-ratchet";
import { seedConnectWizardFixtureEngine, seedDesktopMaxState } from "./max-state-seed";
import { DESKTOP_STATE_REGISTRY } from "./screens-states";

const DESKTOP_AUDIT_BASELINE = loadRatchetedDesktopBaseline();

interface UiSpecJson {
  readonly desktopAudit: {
    readonly fitHeadroomPx: number;
    readonly anchorTolerancePx: number;
    readonly bandEpsilonPx: number;
    readonly minRepeatingGroupSize: number;
  };
}

const here = path.dirname(fileURLToPath(import.meta.url));
const uiSpec = JSON.parse(
  fs.readFileSync(path.resolve(here, "..", "..", "src", "ui-spec.json"), "utf-8"),
) as UiSpecJson;
const DESKTOP_SPEC: DesktopAuditSpec = uiSpec.desktopAudit;

const resultsDir = path.resolve(here, "..", "..", "desktop-audit-results");

const DECISION_FIXTURE_TITLE = "e2e-desktop-audit-decision";
const PREP_CHAT_FIXTURE_TITLE = "e2e-desktop-audit-prep-chat";
const ENGINE_CONFIG_FIXTURE_OWNER = "e2e-mobile-audit-co";
const ENGINE_CONFIG_FIXTURE_REPO = "engine-config-fixture";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf-8").trim();
}

async function seedWorkspaceDecisionStandard(request: APIRequestContext, root: string): Promise<string> {
  const list = await request.get("/api/workspaces");
  if (list.ok()) {
    const rows = (await list.json()) as readonly { task_id: string; title: string }[];
    const existing = rows.find((row) => row.title === DECISION_FIXTURE_TITLE);
    if (existing !== undefined) {
      seedBlockingQuestion(root, existing.task_id, "Which storage backend?", {
        status: "escalated",
        parkReason: null,
        proposedAnswerSpace: ["duckdb", "postgres"],
        recommended: "duckdb",
        questionClass: "orchestrated_completeness",
      });
      return existing.task_id;
    }
  }
  const create = await request.post("/api/workspaces", {
    data: { title: DECISION_FIXTURE_TITLE, description: "desktop-audit fixture workspace" },
  });
  if (!create.ok()) {
    throw new Error(`desktop-audit: failed to seed decision fixture workspace (${String(create.status())})`);
  }
  const { task_id: taskId } = (await create.json()) as { task_id: string };
  seedBlockingQuestion(root, taskId, "Which storage backend?", {
    status: "escalated",
    parkReason: null,
    proposedAnswerSpace: ["duckdb", "postgres"],
    recommended: "duckdb",
    questionClass: "orchestrated_completeness",
  });
  return taskId;
}

async function seedPrepChatStandard(request: APIRequestContext): Promise<string> {
  const list = await request.get("/api/workspaces");
  if (list.ok()) {
    const rows = (await list.json()) as readonly { task_id: string; title: string }[];
    const existing = rows.find((row) => row.title === PREP_CHAT_FIXTURE_TITLE);
    if (existing !== undefined) {
      const detail = await request.get(`/api/workspaces/${existing.task_id}`);
      if (detail.ok()) {
        const { prep_chat: prepChat } = (await detail.json()) as { prep_chat: unknown };
        if (prepChat != null) return existing.task_id;
      }
      await request.delete(`/api/workspaces/${existing.task_id}`);
    }
  }
  const chatCreate = await request.post("/api/build-chat/sessions", {
    data: { engine_key: "DejvMatikar/alan" },
  });
  if (!chatCreate.ok()) {
    throw new Error(`desktop-audit: failed to seed prep-chat fixture build session (${String(chatCreate.status())})`);
  }
  const { session_id: sessionId } = (await chatCreate.json()) as { session_id: string };
  const create = await request.post("/api/workspaces", {
    data: {
      title: PREP_CHAT_FIXTURE_TITLE,
      description: "desktop-audit prep-chat fixture workspace",
      team: "implementation_plan",
      prep_chat_kind: "build",
      prep_chat_session_id: sessionId,
    },
  });
  if (!create.ok()) {
    throw new Error(`desktop-audit: failed to seed prep-chat fixture workspace (${String(create.status())})`);
  }
  const { task_id: taskId } = (await create.json()) as { task_id: string };
  return taskId;
}

async function ensureEngineConfigFixtureEngine(request: APIRequestContext, root: string): Promise<void> {
  const headers = { "X-Alan-Control-Token": readControlToken(root) };
  const res = await request.post("/api/engines", {
    data: { repo: { owner: ENGINE_CONFIG_FIXTURE_OWNER, repo: ENGINE_CONFIG_FIXTURE_REPO } },
    headers,
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(`desktop-audit: failed to register engine-config fixture engine (${String(res.status())})`);
  }
}

async function seedStandardState(
  page: Page,
  request: APIRequestContext,
  root: string,
): Promise<Readonly<Record<string, string>>> {
  await seedProjectFeatures(page, true);
  await seedConnectWizardFixtureEngine(request, root, []);
  const decisionTaskId = await seedWorkspaceDecisionStandard(request, root);
  const prepChatTaskId = await seedPrepChatStandard(request);
  await ensureEngineConfigFixtureEngine(request, root);
  return { taskId: decisionTaskId, prepChatTaskId };
}

function resolveScreens(
  screens: readonly AuditScreen[],
  params: Readonly<Record<string, string>>,
): readonly AuditScreen[] {
  return screens.map((screen) => {
    let route = screen.route;
    for (const [key, value] of Object.entries(params)) {
      route = route.replace(`:${key}`, value);
    }
    return route === screen.route ? screen : { ...screen, route };
  });
}

async function waitForInteractiveCountStable(page: Page): Promise<void> {
  const requiredStableSamples = 3;
  const maxWaitMs = 10_000;
  await page
    .waitForFunction(
      (required) => {
        const w = window as unknown as { __desktopAuditStable?: { prev: number; streak: number } };
        const state = w.__desktopAuditStable ?? { prev: -1, streak: 0 };
        const interactive = document.querySelectorAll<HTMLElement>(
          'a[href], button, [role="button"], input, select, textarea',
        );
        const count = Array.from(interactive).filter((el) => {
          const rect = el.getBoundingClientRect();
          return el.offsetParent !== null && rect.width > 0 && rect.height > 0;
        }).length;
        const streak = count === state.prev ? state.streak + 1 : 1;
        w.__desktopAuditStable = { prev: count, streak };
        return streak >= required;
      },
      requiredStableSamples,
      { polling: 250, timeout: maxWaitMs },
    )
    .catch(() => undefined);
}

test("desktop state registry covers every screen with a valid entry", () => {
  const result = validateDesktopStateRegistry(
    SCREENS.map((screen) => screen.name),
    DESKTOP_STATE_REGISTRY,
  );
  expect(result.valid, result.errors.join("; ")).toBe(true);
});

test("the standard and max desktop audit servers' engines roots hold the test default profile byte for byte", () => {
  const expected = fs.readFileSync(TEST_DEFAULT_PROFILE);
  for (const root of [resolveDesktopAuditStandardRoot(), resolveDesktopAuditMaxRoot()]) {
    const installed = installedDefaultProfile(root);
    expect(fs.existsSync(installed)).toBe(true);
    expect(fs.readFileSync(installed).equals(expected)).toBe(true);
  }
});

test("captures a machine-readable desktop-UI audit for every configured screen and state", async ({
  page,
  request,
}) => {
  fs.mkdirSync(resultsDir, { recursive: true });

  const projectName = test.info().project.name;
  const state: DesktopAuditState = projectName === "desktop-max" ? "max" : "standard";
  const root = state === "max" ? resolveDesktopAuditMaxRoot() : resolveDesktopAuditStandardRoot();

  const registryEntries = new Map(DESKTOP_STATE_REGISTRY.map((entry) => [entry.screen, entry]));
  const applicableScreens =
    state === "standard"
      ? SCREENS
      : SCREENS.filter((screen) => registryEntries.get(screen.name)?.maxState.kind === "seeded");

  const routeParams =
    state === "max"
      ? (await seedDesktopMaxState(page, request, root)).routeParams
      : await seedStandardState(page, request, root);

  const resolvedScreens = resolveScreens(applicableScreens, routeParams);
  const screenReports: DesktopScreenReport[] = [];

  for (const screen of resolvedScreens) {
    await page.goto(screen.route);
    await page.waitForLoadState("load");
    await waitForInteractiveCountStable(page);

    const tree = await page.evaluate(extractGeometryTree, {
      minRepeatingGroupSize: DESKTOP_SPEC.minRepeatingGroupSize,
    });

    const screenReport = buildDesktopScreenReport(screen.name, state, screen.route, tree, DESKTOP_SPEC);
    screenReports.push(screenReport);

    const screenAuditPath = path.join(resultsDir, `${screen.name}@${state}.audit.json`);
    fs.writeFileSync(screenAuditPath, JSON.stringify(screenReport, null, 2));

    const screenshotFile = `${screen.name}@${state}.png`;
    await page.screenshot({ path: path.join(resultsDir, screenshotFile), fullPage: true });
  }

  const reportPath = path.join(resultsDir, `report-${state}.json`);
  fs.writeFileSync(reportPath, JSON.stringify(screenReports, null, 2));

  const comparison = compareToBaseline(screenReports, DESKTOP_AUDIT_BASELINE);
  expect(comparison.pass, formatComparisonDiff(comparison, "desktop-UI")).toBe(true);
});
