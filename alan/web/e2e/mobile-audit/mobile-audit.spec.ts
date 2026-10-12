import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  buildMobileAuditReport,
  buildScreenReport,
  type AccessibilityViolation,
  type ScreenAuditReport,
  type TapTargetElement,
} from "../../src/mobile-audit/audit-helpers";
import { compareToBaseline } from "../../src/mobile-audit/baseline-compare";
import { formatComparisonDiff } from "../../src/ui-audit/baseline-ratchet";
import { resolveMobileAuditRoot } from "../../src/mobile-audit-config";
import { installedDefaultProfile, TEST_DEFAULT_PROFILE } from "../fixtures/default-profile";
import { seedBlockingQuestion, seedProjectFeatures } from "../fixtures/seed";
import { loadRatchetedBaseline } from "./load-baseline";
import { SCREENS, type AuditScreen } from "./screens";

const MOBILE_AUDIT_BASELINE = loadRatchetedBaseline();

const MOBILE_AUDIT_ROOT = resolveMobileAuditRoot();

const AXE_RULE_IDS = [
  "color-contrast",
  "aria-allowed-attr",
  "aria-required-attr",
  "aria-roles",
  "aria-valid-attr-value",
  "aria-valid-attr",
  "aria-hidden-body",
  "aria-hidden-focus",
];

const here = path.dirname(fileURLToPath(import.meta.url));
const resultsDir = path.resolve(here, "..", "..", "mobile-audit-results");

async function waitForInteractiveCountStable(page: Page): Promise<void> {
  const sampleIntervalMs = 250;
  const requiredStableSamples = 3;
  const maxWaitMs = 10_000;
  const countVisibleInteractive = () =>
    page.evaluate(() => {
      const interactive = document.querySelectorAll<HTMLElement>(
        'a[href], button, [role="button"], input, select, textarea',
      );
      return Array.from(interactive).filter((el) => {
        const rect = el.getBoundingClientRect();
        return el.offsetParent !== null && rect.width > 0 && rect.height > 0;
      }).length;
    });
  const deadline = Date.now() + maxWaitMs;
  let previous = -1;
  let stableSamples = 0;
  while (Date.now() < deadline && stableSamples < requiredStableSamples) {
    const count = await countVisibleInteractive();
    if (count === previous) {
      stableSamples += 1;
    } else {
      stableSamples = 1;
      previous = count;
    }
    await page.waitForTimeout(sampleIntervalMs);
  }
}

const AGENTS_EXPANDED_TEST_ID_PREFIX = "agent-team_role:implementation/implementer";

async function expectAgentsScreenReady(page: Page, screenName: string): Promise<void> {
  if (screenName === "agents") {
    await expect(page.getByTestId("repo-agents-list")).toBeVisible();
    await expect(page.getByTestId("repo-agents-notes")).toBeVisible();
  }
  if (screenName === "agents-expanded") {
    await expect(page.getByTestId(`${AGENTS_EXPANDED_TEST_ID_PREFIX}-prompt-text`)).toBeVisible();
    await expect(page.getByTestId(`${AGENTS_EXPANDED_TEST_ID_PREFIX}-setup`)).toBeVisible();
  }
}

const DECISION_FIXTURE_TITLE = "e2e-mobile-audit-decision";

function seedDecisionHandover(taskId: string): void {
  seedBlockingQuestion(MOBILE_AUDIT_ROOT, taskId, "Which storage backend?", {
    status: "escalated",
    parkReason: null,
    proposedAnswerSpace: ["duckdb", "postgres"],
    recommended: "duckdb",
    questionClass: "orchestrated_completeness",
  });
}

async function seedWorkspaceDecisionFixture(request: APIRequestContext): Promise<string> {
  const list = await request.get("/api/workspaces");
  if (list.ok()) {
    const rows = (await list.json()) as readonly { task_id: string; title: string }[];
    const existing = rows.find((row) => row.title === DECISION_FIXTURE_TITLE);
    if (existing !== undefined) {
      seedDecisionHandover(existing.task_id);
      return existing.task_id;
    }
  }
  const create = await request.post("/api/workspaces", {
    data: { title: DECISION_FIXTURE_TITLE, description: "mobile-audit fixture workspace" },
  });
  if (!create.ok()) {
    throw new Error(`mobile-audit: failed to seed decision fixture workspace (${String(create.status())})`);
  }
  const { task_id: taskId } = (await create.json()) as { task_id: string };
  seedDecisionHandover(taskId);
  return taskId;
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

const ENGINE_CONFIG_FIXTURE_OWNER = "e2e-mobile-audit-co";
const ENGINE_CONFIG_FIXTURE_REPO = "engine-config-fixture";

function readControlToken(): string {
  return fs.readFileSync(path.join(MOBILE_AUDIT_ROOT, "state", "control_token"), "utf-8").trim();
}

async function ensureEngineConfigFixtureEngine(request: APIRequestContext): Promise<void> {
  const res = await request.post("/api/engines", {
    data: { repo: { owner: ENGINE_CONFIG_FIXTURE_OWNER, repo: ENGINE_CONFIG_FIXTURE_REPO } },
    headers: { "X-Alan-Control-Token": readControlToken() },
  });
  if (res.ok() || res.status() === 409) return;
  throw new Error(
    `mobile-audit: failed to register engine-config fixture engine (${String(res.status())})`,
  );
}

const PREP_CHAT_FIXTURE_TITLE = "e2e-mobile-audit-prep-chat";

async function seedPrepChatFixture(request: APIRequestContext): Promise<string> {
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
    throw new Error(
      `mobile-audit: failed to seed prep-chat fixture build session (${String(chatCreate.status())}): ${await chatCreate.text()}`,
    );
  }
  const { session_id: sessionId } = (await chatCreate.json()) as { session_id: string };
  const create = await request.post("/api/workspaces", {
    data: {
      title: PREP_CHAT_FIXTURE_TITLE,
      description: "mobile-audit prep-chat fixture workspace",
      team: "implementation_plan",
      prep_chat_kind: "build",
      prep_chat_session_id: sessionId,
    },
  });
  if (!create.ok()) {
    throw new Error(`mobile-audit: failed to seed prep-chat fixture workspace (${String(create.status())})`);
  }
  const { task_id: taskId } = (await create.json()) as { task_id: string };
  return taskId;
}

const CONNECT_STEP3_FIXTURE_OWNER = "e2e-mobile-audit-connect-co";
const CONNECT_STEP3_FIXTURE_REPO = "step3-fixture";

async function ensureConnectStep3FixtureEngine(request: APIRequestContext): Promise<void> {
  const headers = { "X-Alan-Control-Token": readControlToken() };
  const slug = `${CONNECT_STEP3_FIXTURE_OWNER}/${CONNECT_STEP3_FIXTURE_REPO}`;
  const secretRes = await request.post("/api/secrets", {
    data: { engine: slug, purpose: "transport", value: "e2e-mobile-audit-placeholder-token" },
    headers,
  });
  if (!secretRes.ok() && secretRes.status() !== 409) {
    throw new Error(
      `mobile-audit: failed to seed connect-wizard-step3 fixture secret (${String(secretRes.status())})`,
    );
  }

  const res = await request.post("/api/engines", {
    data: {
      repo: { owner: CONNECT_STEP3_FIXTURE_OWNER, repo: CONNECT_STEP3_FIXTURE_REPO },
      forge: {
        repo: { owner: CONNECT_STEP3_FIXTURE_OWNER, repo: CONNECT_STEP3_FIXTURE_REPO },
        credential_env_name: "E2E_MOBILE_AUDIT_CONNECT_GH_TOKEN",
        authoritative_checks: [],
      },
      preset: "python",
    },
    headers,
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(
      `mobile-audit: failed to register connect-wizard-step3 fixture engine (${String(res.status())})`,
    );
  }

  const deleteRes = await request.delete("/api/secrets", {
    data: { engine: slug, purpose: "transport" },
    headers,
  });
  if (!deleteRes.ok() && deleteRes.status() !== 404) {
    throw new Error(
      `mobile-audit: failed to clean up connect-wizard-step3 fixture secret (${String(deleteRes.status())})`,
    );
  }
}

interface UiSpecJson {
  readonly tapTarget: { readonly minPx: number };
  readonly contrast: {
    readonly normalTextMinRatio: number;
    readonly largeTextMinRatio: number;
  };
  readonly viewport: { readonly floorWidthPx: number };
}

const uiSpec = JSON.parse(
  fs.readFileSync(path.resolve(here, "..", "..", "src", "ui-spec.json"), "utf-8"),
) as UiSpecJson;
const smallestSupportedViewportWidthPx = uiSpec.viewport.floorWidthPx;
const tapTargetMinPx = uiSpec.tapTarget.minPx;
const contrastNormalTextMinRatio = uiSpec.contrast.normalTextMinRatio;
const contrastLargeTextMinRatio = uiSpec.contrast.largeTextMinRatio;

const REDIRECT_GUARDED_ROUTE_PREFIXES = ["/projects", "/repos"];

function isRedirectGuardedRoute(route: string): boolean {
  return REDIRECT_GUARDED_ROUTE_PREFIXES.some((prefix) => route.startsWith(prefix));
}

test("the mobile audit server's engines root holds the test default profile byte for byte", () => {
  const installed = installedDefaultProfile(MOBILE_AUDIT_ROOT);
  expect(fs.existsSync(installed)).toBe(true);
  expect(fs.readFileSync(installed).equals(fs.readFileSync(TEST_DEFAULT_PROFILE))).toBe(true);
});

test("captures a machine-readable mobile-UI audit for every configured screen", async ({
  page,
  request,
}) => {
  fs.mkdirSync(resultsDir, { recursive: true });

  await seedProjectFeatures(page, true);

  const decisionTaskId = await seedWorkspaceDecisionFixture(request);
  const prepChatTaskId = await seedPrepChatFixture(request);
  await ensureEngineConfigFixtureEngine(request);
  await ensureConnectStep3FixtureEngine(request);
  const resolvedScreens = resolveScreens(SCREENS, {
    taskId: decisionTaskId,
    prepChatTaskId,
  });

  const screenReports: ScreenAuditReport[] = [];

  for (const screen of resolvedScreens) {
    await page.goto(screen.route);
    await page.waitForLoadState("load");
    await waitForInteractiveCountStable(page);

    if (isRedirectGuardedRoute(screen.route)) {
      const expectedPathname = screen.route.split("?")[0];
      expect(
        new URL(page.url()).pathname,
        `${screen.name} redirected away from ${screen.route} — is the project-features preference seeded on?`,
      ).toBe(expectedPathname);
    }

    if (screen.name === "workspace-decision") {
      await expect(page.getByTestId("decision-card")).toBeVisible();
      await expect(page.getByTestId("open-question-options")).toBeVisible();
      await expect(page.getByTestId("open-question-option")).toHaveCount(2);
      await expect(page.getByRole("radiogroup")).toHaveCount(0);
    }

    if (screen.name === "prep-chat") {
      await expect(page.getByTestId("prep-chat-kind")).toHaveText("Build conversation");
      await expect(page.getByTestId("prep-chat-status")).toHaveText("Archived — read-only");
    }

    if (screen.name === "repo-engine-config") {
      await expect(page.getByTestId("engine-config-readonly")).toBeVisible();
      await expect(page.getByTestId("engine-ops-posture-tile")).toBeVisible();
    }

    await expectAgentsScreenReady(page, screen.name);

    if (screen.name === "connect-wizard-step4") {
      await expect(page.getByRole("radiogroup")).toBeVisible();
      await expect(page.getByTestId("step4-readiness-chip")).toBeVisible();
    }

    if (screen.name === "health") {
      await expect(page.getByTestId("health-view-paused-notice")).toHaveCount(0);
      const severityBadgeCount = await page
        .locator('[data-testid^="severity-badge-"]')
        .count();
      expect(severityBadgeCount).toBeGreaterThanOrEqual(1);
      const blockerChipCount = await page
        .locator('[data-testid^="blocker-flags-"] > span')
        .count();
      expect(blockerChipCount).toBeGreaterThanOrEqual(1);
      const inProgressChipCount = await page
        .locator('[data-testid^="in-progress-chip-"]')
        .count();
      expect(inProgressChipCount).toBeGreaterThanOrEqual(1);
    }

    const documentWidthPx = await page.evaluate(
      () => document.documentElement.scrollWidth,
    );

    const tapTargetElements: TapTargetElement[] = await page.evaluate(() => {
      const cssPath = (element: Element): string => {
        const parts: string[] = [];
        let node: Element | null = element;
        while (node && node !== document.documentElement) {
          const current: Element = node;
          const parent: Element | null = current.parentElement;
          let part = current.tagName.toLowerCase();
          if (parent) {
            const sameTagSiblings = Array.from(parent.children).filter(
              (sibling) => sibling.tagName === current.tagName,
            );
            if (sameTagSiblings.length > 1) {
              part += `:nth-of-type(${sameTagSiblings.indexOf(current) + 1})`;
            }
          }
          parts.unshift(part);
          node = parent;
        }
        return parts.join(" > ");
      };

      const isVisible = (el: HTMLElement): boolean => {
        const rect = el.getBoundingClientRect();
        return el.offsetParent !== null && rect.width > 0 && rect.height > 0;
      };

      const interactive = document.querySelectorAll<HTMLElement>(
        'a[href], button, [role="button"], input, select, textarea',
      );
      return Array.from(interactive)
        .filter(isVisible)
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const testId = el.getAttribute("data-testid");
          return {
            selector: testId ? `[data-testid="${testId}"]` : cssPath(el),
            widthPx: rect.width,
            heightPx: rect.height,
          };
        });
    });

    const axeResults = await new AxeBuilder({ page })
      .withRules(AXE_RULE_IDS)
      .analyze();
    const accessibilityViolations: AccessibilityViolation[] =
      axeResults.violations.map((violation) => ({
        id: violation.id,
        impact: violation.impact ?? null,
        description: violation.description,
        nodes: violation.nodes.map((node) => ({
          target: node.target.map(String),
          failureSummary: node.failureSummary ?? null,
        })),
      }));

    const screenshotFile = `${screen.name}.png`;
    await page.screenshot({
      path: path.join(resultsDir, screenshotFile),
      fullPage: true,
    });

    const screenReport = buildScreenReport(
      {
        name: screen.name,
        route: screen.route,
        documentWidthPx,
        tapTargetElements,
        accessibilityViolations,
        screenshotPath: screenshotFile,
      },
      {
        viewportWidthPx: smallestSupportedViewportWidthPx,
        tapTargetMinPx,
      },
    );
    screenReports.push(screenReport);

    const screenAuditPath = path.join(resultsDir, `${screen.name}.audit.json`);
    fs.writeFileSync(screenAuditPath, JSON.stringify(screenReport, null, 2));
    expect(fs.existsSync(screenAuditPath)).toBe(true);
  }

  const report = buildMobileAuditReport(
    screenReports,
    {
      viewportWidthPx: smallestSupportedViewportWidthPx,
      tapTargetMinPx,
      contrastNormalTextMinRatio,
      contrastLargeTextMinRatio,
    },
    new Date().toISOString(),
  );

  const reportPath = path.join(resultsDir, "report.json");
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));

  expect(fs.existsSync(reportPath)).toBe(true);

  const comparison = compareToBaseline(report, MOBILE_AUDIT_BASELINE);

  expect(comparison.pass, formatComparisonDiff(comparison, "mobile-UI")).toBe(true);
});
