import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";

const REPO = "DejvMatikar/alan";
const ISSUE_URL = "https://example.test/issues/4242";
const MOBILE_VIEWPORT = { width: 375, height: 800 };
const MIN_TAP_PX = 44;

const WINDOW = {
  window_days: 30,
  events_total: 0,
  workspaces_touched: 0,
  waited_workspaces: 0,
  diagnosed_workspaces: 0,
  diagnosable_workspaces: 0,
  still_stuck_workspaces: 0,
  gave_up_workspaces: 0,
  aggregates: [] as unknown[],
};

const FLEET_AGGREGATE = {
  shape: "fleet_only_shape",
  total: 9,
  workspaces: 5,
  cleared_workspaces: 3,
  still_stuck_workspaces: 1,
  gone_workspaces: 1,
  retried_workspaces: 2,
  attempt_outcomes: [{ outcome: "acted", count: 7 }],
  report_outcomes: [{ outcome: "needs_human", count: 2 }],
};

const REPO_AGGREGATE = {
  ...FLEET_AGGREGATE,
  shape: "repo_only_shape",
  total: 3,
  workspaces: 2,
};

const FLEET_ACTIVITY = {
  ...WINDOW,
  scope: "fleet",
  events_total: 9,
  workspaces_touched: 5,
  waited_workspaces: 1,
  diagnosed_workspaces: 1,
  diagnosable_workspaces: 4,
  still_stuck_workspaces: 1,
  gave_up_workspaces: 2,
  aggregates: [FLEET_AGGREGATE],
  ever_recorded: true,
  unattributed_events: 0,
};

const REPO_EVENTS = [
  {
    id: 3,
    at: "2026-09-19T14:05Z",
    workspace_key: "ws-alpha",
    shape: "repo_only_shape",
    kind: "attempt",
    action: "rebase_branch",
    outcome: "failed",
    reason: null,
    authority_holder: null,
    clearance: "still_stuck",
    attempt: { fingerprint_index: 2, fingerprint_ceiling: 2, workspace_index: 2, workspace_ceiling: 3 },
    attempts_total: null,
    issue_url: ISSUE_URL,
    diagnosis: { signature: "sig-flaky-ci", proposed_disposition: "known_flake", verified: false },
  },
  {
    id: 2,
    at: "2026-09-18T09:30Z",
    workspace_key: "ws-beta",
    shape: "repo_only_shape",
    kind: "report",
    action: null,
    outcome: "needs_human",
    reason: "ceiling_reached",
    authority_holder: "human_operator",
    clearance: "unknown",
    attempt: null,
    attempts_total: 3,
    issue_url: null,
    diagnosis: null,
  },
  {
    id: 1,
    at: "2026-09-17T08:00Z",
    workspace_key: "ws-alpha",
    shape: "repo_only_shape",
    kind: "attempt",
    action: "rebase_branch",
    outcome: "acted",
    reason: null,
    authority_holder: null,
    clearance: "cleared",
    attempt: { fingerprint_index: 1, fingerprint_ceiling: 2, workspace_index: 1, workspace_ceiling: 3 },
    attempts_total: null,
    issue_url: null,
    diagnosis: null,
  },
];

const REPO_WORKSPACES = [
  {
    workspace_key: "ws-calm",
    last_at: "2026-09-19T20:00Z",
    shape: "repo_only_shape",
    events_total: 4,
    events_listed: 0,
    attempts_total: 1,
    attempts_ceiling: 5,
    clearance: "cleared",
    gave_up: false,
    issue_url: null,
    diagnosis: null,
  },
  {
    workspace_key: "ws-beta",
    last_at: "2026-09-18T09:30Z",
    shape: "repo_only_shape",
    events_total: 1,
    events_listed: 1,
    attempts_total: 3,
    attempts_ceiling: 5,
    clearance: "unknown",
    gave_up: true,
    issue_url: null,
    diagnosis: null,
  },
  {
    workspace_key: "ws-alpha",
    last_at: "2026-09-19T14:05Z",
    shape: "repo_only_shape",
    events_total: 2,
    events_listed: 2,
    attempts_total: 2,
    attempts_ceiling: 5,
    clearance: "still_stuck",
    gave_up: false,
    issue_url: ISSUE_URL,
    diagnosis: { signature: "sig-flaky-ci", proposed_disposition: "known_flake", verified: false },
  },
];

const REPO_ACTIVITY = {
  ...WINDOW,
  scope: "repo",
  repo: REPO,
  events_total: 7,
  workspaces_touched: 3,
  diagnosed_workspaces: 1,
  diagnosable_workspaces: 2,
  still_stuck_workspaces: 1,
  gave_up_workspaces: 1,
  aggregates: [REPO_AGGREGATE],
  fleet_ever_recorded: true,
  events: REPO_EVENTS,
  workspaces: REPO_WORKSPACES,
};

const HEALTH_WITH_FINDING = {
  ok: true,
  queue_paused: false,
  paused_until: null,
  health_state: {
    verdict: "degraded",
    blocked_count: 1,
    in_flight_count: 0,
    findings: [
      {
        finding_id: "e2e-finding-1",
        kind: "build_task",
        severity: "critical",
        description: "E2E finding awaiting attention",
        workspace_id: "e2e-finding-ws",
        pr_number: null,
        blocker_flags: [],
        action: "deep_link",
        engine_key: REPO,
      },
    ],
    computed_at: new Date().toISOString(),
  },
};

type Payload = Record<string, unknown>;

interface Stub {
  readonly engineParams: (string | null)[];
  readonly nonGetRequests: string[];
}

async function stubHealth(
  page: Page,
  options: { fleet?: Payload; repo?: Payload; withFinding?: boolean } = {},
): Promise<Stub> {
  const stub: Stub = { engineParams: [], nonGetRequests: [] };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    if (request.method() !== "GET") stub.nonGetRequests.push(`${request.method()} ${request.url()}`);
    const url = new URL(request.url());
    if (url.pathname === "/api/health/unblocker-activity") {
      const engine = url.searchParams.get("engine");
      stub.engineParams.push(engine);
      await route.fulfill({
        json: engine === null ? (options.fleet ?? FLEET_ACTIVITY) : (options.repo ?? REPO_ACTIVITY),
      });
      return;
    }
    if (url.pathname === "/api/health" && options.withFinding === true) {
      await route.fulfill({ json: HEALTH_WITH_FINDING });
      return;
    }
    await route.continue();
  });
  return stub;
}

async function tabUntilVisited(page: Page, targets: readonly string[]): Promise<string[]> {
  const visited: string[] = [];
  for (let presses = 0; presses < 250; presses += 1) {
    await page.keyboard.press("Tab");
    visited.push(
      await page.evaluate(() => {
        const el = document.activeElement;
        if (el === null) return "";
        return el.getAttribute("href") ?? el.getAttribute("aria-label") ?? el.textContent ?? el.tagName;
      }),
    );
    if (targets.every((target) => visited.some((stop) => stop.includes(target)))) break;
  }
  return visited;
}

function scopedUrl(): string {
  return `/health?engine=${encodeURIComponent(REPO)}`;
}

async function expandPanel(page: Page): Promise<void> {
  await page.getByTestId("unblocker-activity-toggle").click();
  await expect(page.getByTestId("unblocker-activity-disclosure")).toHaveJSProperty("open", true);
}

test.describe("Health — unblocker activity panel", { tag: "@no-claude" }, () => {
  test("arrives collapsed with a summary line, and one expand reveals the park-kinds table", async ({
    page,
  }) => {
    const stub = await stubHealth(page);
    await page.goto("/health");
    const panel = page.getByTestId("unblocker-activity-panel");
    await expect(panel.getByRole("heading", { name: "Unblocker activity" })).toBeVisible();
    await expect(panel).toContainText("last 30 days");
    const disclosure = page.getByTestId("unblocker-activity-disclosure");
    await expect(disclosure).toHaveJSProperty("open", false);
    await expect(page.getByTestId("unblocker-activity-toggle")).toContainText(
      "9 events · 5 workspaces touched · 1 still stuck · 2 gave up",
    );
    await expect(page.getByTestId("unblocker-aggregates-table")).toBeHidden();
    await expandPanel(page);
    await expect(page.getByTestId("unblocker-aggregate-fleet_only_shape")).toBeVisible();
    await expect(page.getByTestId("unblocker-metric-diagnosis")).toContainText("1 of 4");
    expect(stub.engineParams.length).toBeGreaterThan(0);
    expect(stub.engineParams.every((engine) => engine === null)).toBe(true);
  });

  test("fleet view states why the per-workspace table is unavailable instead of rendering an empty one", async ({
    page,
  }) => {
    await stubHealth(page);
    await page.goto("/health");
    await expandPanel(page);
    await expect(page.getByTestId("unblocker-aggregates-table")).toBeVisible();
    await expect(page.getByTestId("unblocker-workspaces-unavailable")).toBeVisible();
    await expect(page.getByTestId("unblocker-workspaces-unavailable")).toContainText(
      "one repository at a time",
    );
    await expect(page.getByTestId("unblocker-workspaces-table")).toHaveCount(0);
    await expect(page.locator('[data-testid^="unblocker-event-"]')).toHaveCount(0);
    await expect(page.getByTestId("unblocker-activity-panel").getByRole("link")).toHaveCount(0);
  });

  test("scoped view reveals both tables in one expand, with the park kinds compact and the fleet's figures absent", async ({
    page,
  }) => {
    const stub = await stubHealth(page);
    await page.goto(scopedUrl());
    await expect(page.getByTestId("unblocker-activity-disclosure")).toHaveJSProperty("open", false);
    await expandPanel(page);
    await expect(page.getByTestId("unblocker-aggregates-table")).toBeVisible();
    await expect(page.getByTestId("unblocker-workspaces-table")).toBeVisible();
    await expect(page.getByTestId("unblocker-aggregate-repo_only_shape")).toBeVisible();
    await expect(page.getByTestId("unblocker-aggregate-fleet_only_shape")).toHaveCount(0);
    await expect(page.getByTestId("unblocker-activity-panel")).not.toContainText("all repositories");
    expect(stub.engineParams).toContain(REPO);
    expect(stub.engineParams).not.toContain(null);
  });

  test("lists one row per touched workspace with still-stuck and gave-up first, then most recent", async ({
    page,
  }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    const order = await page
      .locator('[data-testid^="unblocker-workspace-group-"]')
      .evaluateAll((groups) => groups.map((group) => group.getAttribute("data-testid")));
    expect(order).toEqual([
      "unblocker-workspace-group-ws-alpha",
      "unblocker-workspace-group-ws-beta",
      "unblocker-workspace-group-ws-calm",
    ]);
    await expect(page.getByTestId("unblocker-workspaces")).toContainText("3 touched");
    await expect(page.getByTestId("unblocker-metric-touched")).toContainText("3");
  });

  test("shows the aggregated fields on each workspace row", async ({ page }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    const alpha = page.getByTestId("unblocker-workspace-ws-alpha");
    await expect(alpha.getByRole("link", { name: "ws-alpha" })).toHaveAttribute("href", "/workspaces/ws-alpha");
    await expect(alpha).toContainText("2026-09-19 14:05 UTC");
    await expect(alpha).toContainText("repo_only_shape");
    await expect(alpha).toContainText("Attempt 2 of 5");
    await expect(page.getByTestId("aggregate-chip-unblocker-workspace-clearance-ws-alpha")).toContainText(
      "still stuck",
    );
    await expect(alpha).toContainText("sig-flaky-ci");
    await expect(alpha).toContainText("unverified");
    const issueLink = page.getByTestId("unblocker-issue-link-ws-alpha");
    await expect(issueLink).toHaveAttribute("href", ISSUE_URL);
    await expect(issueLink).toHaveAttribute("target", "_blank");
    await expect(issueLink).toContainText("(opens in a new tab)");
    await expect(page.getByTestId("aggregate-chip-unblocker-workspace-gave-up-ws-beta")).toContainText("Gave up");
    await expect(page.getByTestId("aggregate-chip-unblocker-workspace-clearance-ws-beta")).toContainText(
      "workspace gone",
    );
    await expect(page.getByTestId("aggregate-chip-unblocker-workspace-clearance-ws-calm")).toContainText("cleared");
    await expect(page.getByTestId("unblocker-workspace-ws-calm")).toContainText("Attempt 1 of 5");
  });

  test("expands a workspace row to its single-line events and collapses it again", async ({ page }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    const toggle = page.getByTestId("unblocker-workspace-toggle-ws-alpha");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("unblocker-event-3")).toBeHidden();
    await expect(page.getByTestId("unblocker-event-1")).toBeHidden();
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("unblocker-event-3")).toBeVisible();
    await expect(page.getByTestId("unblocker-event-1")).toBeVisible();
    await expect(page.getByTestId("unblocker-event-2")).toBeHidden();
    await expect(page.getByTestId("aggregate-chip-unblocker-repetition-3")).toContainText(
      "attempt 2 of 2 — the host stops after this",
    );
    await expect(page.getByTestId("aggregate-chip-unblocker-repetition-1")).toContainText("attempt 1 of 2");
    await expect(page.getByTestId("aggregate-chip-unblocker-clearance-3")).toContainText("still stuck");
    await expect(page.getByTestId("aggregate-chip-unblocker-clearance-1")).toContainText("cleared");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("unblocker-event-3")).toBeHidden();
  });

  test("shows a report event as gave up with its reason and authority holder", async ({ page }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    await page.getByTestId("unblocker-workspace-toggle-ws-beta").click();
    const event = page.getByTestId("unblocker-event-2");
    await expect(event).toBeVisible();
    await expect(page.getByTestId("aggregate-chip-unblocker-gave-up-2")).toContainText("gave up after 3 attempts");
    await expect(event).toContainText("Reason ceiling_reached");
    await expect(event).toContainText("Authority holder human_operator");
  });

  test("says showing k of n events on a row whose events were cut by the cap", async ({ page }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    const note = page.getByTestId("unblocker-workspace-cap-ws-calm");
    await expect(note).toBeHidden();
    await page.getByTestId("unblocker-workspace-toggle-ws-calm").click();
    await expect(note).toBeVisible();
    await expect(note).toContainText("Showing 0 of 4 events");
    await expect(page.getByTestId("unblocker-workspace-cap-ws-alpha")).toHaveCount(0);
    await expect(page.getByTestId("unblocker-events-cap")).toContainText("latest 3 of 7");
  });

  test("keeps the explanation that cleared is not proof the action worked", async ({ page }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    await expect(page.getByTestId("unblocker-clearance-note")).toContainText(
      "not proof that the unblocker's action worked",
    );
  });

  test("says nothing happened in the window when history exists but the window is empty", async ({
    page,
  }) => {
    await stubHealth(page, { fleet: { ...FLEET_ACTIVITY, ...WINDOW, ever_recorded: true } });
    await page.goto("/health");
    await expect(page.getByTestId("unblocker-activity-empty-window")).toContainText(
      "Nothing happened in the last 30 days",
    );
    await expect(page.getByTestId("unblocker-activity-empty-never")).toHaveCount(0);
    await expect(page.getByTestId("unblocker-activity-disclosure")).toHaveCount(0);
  });

  test("says the unblocker has never recorded anything when the ledger is empty", async ({
    page,
  }) => {
    await stubHealth(page, { fleet: { ...FLEET_ACTIVITY, ...WINDOW, ever_recorded: false } });
    await page.goto("/health");
    await expect(page.getByTestId("unblocker-activity-empty-never")).toContainText(
      "The unblocker has never recorded any activity",
    );
    await expect(page.getByTestId("unblocker-activity-empty-window")).toHaveCount(0);
  });

  test("says a repository had no activity while the fleet did elsewhere", async ({ page }) => {
    await stubHealth(page, {
      repo: { ...REPO_ACTIVITY, ...WINDOW, events: [], workspaces: [], fleet_ever_recorded: true },
    });
    await page.goto(scopedUrl());
    await expect(page.getByTestId("unblocker-activity-empty-repo-window")).toContainText(
      `No unblocker activity for ${REPO} in the last 30 days`,
    );
  });

  test("shows a visible error state distinct from the empty states when the endpoint fails", async ({
    page,
  }) => {
    await page.route("**/api/health/unblocker-activity", async (route) => {
      await route.fulfill({ status: 500, json: { detail: "boom" } });
    });
    await page.goto("/health");
    await expect(page.getByTestId("unblocker-activity-error")).toBeVisible();
    await expect(page.getByTestId("unblocker-activity-empty-window")).toHaveCount(0);
    await expect(page.getByTestId("unblocker-activity-empty-never")).toHaveCount(0);
  });
});

test.describe("Health — unblocker activity placement and keyboard", { tag: "@no-claude" }, () => {
  test("scoped page order is health metrics, recurring processes, unblocker activity, other findings", async ({
    page,
  }) => {
    await stubHealth(page, { withFinding: true });
    await page.goto(scopedUrl());
    const metrics = page.getByTestId("health-metrics-panel");
    const recurring = page.getByTestId(`recurring-processes-panel-${REPO}`);
    const unblocker = page.getByTestId("unblocker-activity-panel");
    const findings = page.getByTestId("health-findings-disclosure");
    await expect(findings).toBeVisible();
    await expect(recurring).toBeVisible();
    const tops = await Promise.all(
      [metrics, recurring, unblocker, findings].map(async (locator) => (await locator.boundingBox())!.y),
    );
    expect(tops).toEqual([...tops].sort((a, b) => a - b));
    expect(new Set(tops).size).toBe(4);
  });

  test("fleet page keeps unblocker activity immediately above other health findings", async ({ page }) => {
    await stubHealth(page, { withFinding: true });
    await page.goto("/health");
    const unblocker = page.getByTestId("unblocker-activity-panel");
    const findings = page.getByTestId("health-findings-disclosure");
    await expect(findings).toBeVisible();
    const unblockerBox = (await unblocker.boundingBox())!;
    const findingsBox = (await findings.boundingBox())!;
    expect(unblockerBox.y + unblockerBox.height).toBeLessThanOrEqual(findingsBox.y + 1);
    await expect(page.getByTestId("repo-health-panel")).toBeVisible();
    const tableBox = (await page.getByTestId("repo-health-panel").boundingBox())!;
    expect(tableBox.y).toBeLessThan(unblockerBox.y);
  });

  test("keeps the findings behind a closed disclosure and offers no disposition control", async ({
    page,
  }) => {
    await stubHealth(page, { withFinding: true });
    await page.goto("/health");
    const disclosure = page.getByTestId("health-findings-disclosure");
    await expect(disclosure).toBeVisible();
    await expect(disclosure).not.toHaveAttribute("open", "");
    await expect(page.getByTestId("health-findings-table")).toBeHidden();
    await disclosure.locator("summary").click();
    await expect(disclosure).toHaveAttribute("open", "");
    await expect(page.getByTestId("finding-row-e2e-finding-1")).toBeVisible();
    await expect(page.locator('[data-testid^="disposition-"]')).toHaveCount(0);
    await expect(page.locator('[data-testid^="finding-disposition-"]')).toHaveCount(0);
    await expect(page.getByRole("combobox")).toHaveCount(0);
    await expect(page.getByRole("columnheader", { name: "Disposition" })).toHaveCount(0);
  });

  test("is fully operable by keyboard: the panel disclosure, every row toggle and link, and the findings disclosure", async ({
    page,
  }) => {
    await stubHealth(page, { withFinding: true });
    await page.goto(scopedUrl());
    const disclosure = page.getByTestId("unblocker-activity-disclosure");
    await expect(disclosure).toHaveJSProperty("open", false);
    await page.getByTestId("unblocker-activity-toggle").focus();
    await expect(page.getByTestId("unblocker-activity-toggle")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(disclosure).toHaveJSProperty("open", true);
    const targets = [
      "Events for ws-alpha",
      "/workspaces/ws-alpha",
      ISSUE_URL,
      "Events for ws-beta",
      "/workspaces/ws-beta",
      "Events for ws-calm",
      "Other health findings (1)",
    ];
    const visited = await tabUntilVisited(page, targets);
    for (const target of targets) {
      expect(visited.some((stop) => stop.includes(target))).toBe(true);
    }
    const findings = page.getByTestId("health-findings-disclosure");
    await expect(findings.locator("summary")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(findings).toHaveAttribute("open", "");
    await page.keyboard.press(" ");
    await expect(findings).not.toHaveAttribute("open", "");
  });

  test("toggles a workspace row with Enter and Space and announces the state", async ({ page }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    const toggle = page.getByTestId("unblocker-workspace-toggle-ws-alpha");
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("unblocker-event-3")).toBeVisible();
    await page.keyboard.press(" ");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("unblocker-event-3")).toBeHidden();
  });

  test("fires no non-GET request while the page loads, refetches and toggles the disclosures", async ({
    page,
  }) => {
    const stub = await stubHealth(page, { withFinding: true });
    await page.goto("/health");
    await expandPanel(page);
    await expect(page.getByTestId("unblocker-aggregate-fleet_only_shape")).toBeVisible();
    await page.getByTestId("health-findings-disclosure").locator("summary").click();
    await page.goto(scopedUrl());
    await expandPanel(page);
    await page.getByTestId("unblocker-workspace-toggle-ws-alpha").click();
    await expect(page.getByTestId("unblocker-event-3")).toBeVisible();
    expect(stub.nonGetRequests).toEqual([]);
  });

  test("offers no control on the surface except the disclosure, the row toggles and links", async ({
    page,
  }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    const panel = page.getByTestId("unblocker-activity-panel");
    await expect(panel.locator("input, select, textarea, form")).toHaveCount(0);
    await expect(panel.getByRole("button")).toHaveCount(3);
    await expect(panel.getByRole("button", { name: /^Events for / })).toHaveCount(3);
  });
});

test.describe("Health — unblocker activity at a phone width", { tag: "@no-claude" }, () => {
  test.use({ viewport: MOBILE_VIEWPORT });

  test("keeps the expanded tables inside scrolling regions with no page-level overflow", async ({
    page,
  }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    await page.getByTestId("unblocker-workspace-toggle-ws-alpha").click();
    await expect(page.getByTestId("unblocker-event-3")).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    const regions = page.getByLabel(/scroll horizontally for more columns/);
    await expect(regions).toHaveCount(3);
    for (const region of await regions.all()) {
      await expect(region).toHaveCSS("overflow-x", "auto");
    }
  });

  test("keeps every interactive control at the minimum tap target", async ({ page }) => {
    await stubHealth(page);
    await page.goto(scopedUrl());
    await expandPanel(page);
    const controls = [
      page.getByTestId("unblocker-activity-toggle"),
      page.getByTestId("unblocker-workspace-toggle-ws-alpha"),
      page.getByTestId("unblocker-issue-link-ws-alpha"),
      page.getByRole("link", { name: "ws-alpha" }),
    ];
    for (const control of controls) {
      const box = (await control.boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(MIN_TAP_PX);
    }
    const toggleBox = (await page.getByTestId("unblocker-workspace-toggle-ws-alpha").boundingBox())!;
    expect(toggleBox.width).toBeGreaterThanOrEqual(MIN_TAP_PX);
  });
});
