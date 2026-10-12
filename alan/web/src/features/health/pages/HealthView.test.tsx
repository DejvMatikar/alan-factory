import { render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { HealthState } from "../../../api";
import { HealthProvider } from "../../../api/HealthProvider";
import {
  healthWindow,
  metricWindows,
  repoHealthResponse,
  repoRow,
  scopeWindows,
} from "../../../test/healthWindows";
import {
  activateDisclosure,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { server } from "../../../test/server";
import HealthView from "./HealthView";

const BASE_HEALTH = {
  ok: true as const,
  queue_paused: false,
  paused_until: null,
};

function emptyActivityPayload(engine: string | null) {
  const shared = {
    window_days: 30,
    events_total: 0,
    workspaces_touched: 0,
    waited_workspaces: 0,
    diagnosed_workspaces: 0,
    diagnosable_workspaces: 0,
    aggregates: [],
  };
  if (engine === null) {
    return { scope: "fleet", ...shared, ever_recorded: true, unattributed_events: 0 };
  }
  return { scope: "repo", repo: engine, ...shared, fleet_ever_recorded: true, events: [] };
}

function activePayload() {
  return {
    scope: "repo",
    repo: "acme/widget",
    window_days: 30,
    events_total: 1,
    workspaces_touched: 1,
    waited_workspaces: 0,
    diagnosed_workspaces: 0,
    diagnosable_workspaces: 1,
    still_stuck_workspaces: 1,
    gave_up_workspaces: 0,
    aggregates: [
      {
        shape: "merge_park",
        total: 1,
        workspaces: 1,
        cleared_workspaces: 0,
        still_stuck_workspaces: 1,
        gone_workspaces: 0,
        retried_workspaces: 0,
        attempt_outcomes: [{ outcome: "failed", count: 1 }],
        report_outcomes: [],
      },
    ],
    fleet_ever_recorded: true,
    events: [
      {
        id: 1,
        at: "2026-09-19T14:05Z",
        workspace_key: "ws-alpha",
        shape: "merge_park",
        kind: "attempt",
        action: "rebase_branch",
        outcome: "failed",
        reason: null,
        authority_holder: null,
        clearance: "still_stuck",
        attempt: null,
        attempts_total: null,
        issue_url: null,
        diagnosis: null,
      },
    ],
    workspaces: [
      {
        workspace_key: "ws-alpha",
        last_at: "2026-09-19T14:05Z",
        shape: "merge_park",
        events_total: 1,
        events_listed: 1,
        attempts_total: 1,
        attempts_ceiling: 5,
        clearance: "still_stuck",
        gave_up: false,
        issue_url: null,
        diagnosis: null,
      },
    ],
  };
}

beforeEach(() => {
  server.use(
    http.get("/api/health/unblocker-activity", ({ request }) =>
      HttpResponse.json(emptyActivityPayload(new URL(request.url).searchParams.get("engine"))),
    ),
  );
});

function renderView(initialPath = "/health") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <HealthProvider>
        <HealthView />
      </HealthProvider>
    </MemoryRouter>,
  );
}

function _makeHealthyState(): HealthState {
  return {
    verdict: "healthy",
    blocked_count: 0,
    in_flight_count: 0,
    findings: [],
    computed_at: new Date().toISOString(),
  };
}

function _makeDegradedState(): HealthState {
  return {
    verdict: "degraded",
    blocked_count: 2,
    in_flight_count: 0,
    findings: [
      {
        finding_id: "task-123",
        kind: "build_task",
        severity: "critical",
        description: "Build task task-123 has failing CI checks (PR #42)",
        workspace_id: "task-123",
        pr_number: 42,
        blocker_flags: ["pr_checks_failing"],
        action: "dispatch_remediation",
        in_flight: false,
        engine_key: "DejvMatikar/alan",
      },
      {
        finding_id: "task-456",
        kind: "build_task",
        severity: "medium",
        description: "Build task task-456 is awaiting human input",
        workspace_id: "task-456",
        pr_number: null,
        blocker_flags: ["ws_stuck"],
        action: "deep_link",
        in_flight: false,
      },
    ],
    computed_at: new Date().toISOString(),
  };
}

describe("HealthView", () => {
  it("renders the heading", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: _makeHealthyState() }),
      ),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "System Health" })).toBeInTheDocument();
    });
  });

  it("renders repo-health-panel between the header and the stale notice, in DOM order", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("repo-health-panel")).toBeInTheDocument();
    });
    const heading = screen.getByRole("heading", { name: "System Health" });
    const panel = screen.getByTestId("repo-health-panel");
    const stale = screen.getByTestId("health-view-stale");
    expect(heading.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(panel.compareDocumentPosition(stale) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders repo-health-panel even when healthState is null, and never health-metrics-panel unscoped", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-view-stale")).toBeInTheDocument();
    });
    expect(screen.getByTestId("repo-health-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("health-metrics-panel")).not.toBeInTheDocument();
  });

  it("still renders the findings table below the panel when findings exist", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: _makeDegradedState() }),
      ),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-findings-table")).toBeInTheDocument();
    });
    const panel = screen.getByTestId("repo-health-panel");
    const table = screen.getByTestId("health-findings-table");
    expect(panel.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("still renders the empty state below the panel when there are no findings", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: _makeHealthyState() }),
      ),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-view-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("repo-health-panel")).toBeInTheDocument();
  });

  it("shows stale notice when health_state is null", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-view-stale")).toBeInTheDocument();
    });
  });

  it("shows empty state when no findings", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: _makeHealthyState() }),
      ),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-view-empty")).toBeInTheDocument();
    });
  });

  it("shows findings table when findings exist", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: _makeDegradedState() }),
      ),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-findings-table")).toBeInTheDocument();
    });
  });

  it("never renders a local queue-paused notice, even when the queue is paused", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ok: true,
          queue_paused: true,
          paused_until: Date.now() / 1000 + 3600,
          health_state: _makeHealthyState(),
        }),
      ),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-view-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("health-view-paused-notice")).not.toBeInTheDocument();
  });

  it("shows fallback empty text when verdict is not healthy", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          health_state: {
            verdict: "degraded",
            blocked_count: 0,
            in_flight_count: 0,
            findings: [],
            computed_at: new Date().toISOString(),
          },
        }),
      ),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-view-empty")).toBeInTheDocument();
      expect(screen.getByTestId("health-view-empty").textContent).toContain("No findings");
    });
  });
});

describe("HealthView — hidden repo filtering", () => {
  it("excludes a finding whose engine_key belongs to a hidden repo, keeping a sibling repo's finding", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            { repo: { owner: "DejvMatikar", repo: "alan" } },
            { repo: { owner: "sprocket-co", repo: "sprocket" }, hidden: true },
            { repo: { owner: "sprocket-co", repo: "kept" } },
          ],
        }),
      ),
    );
    const state: HealthState = {
      verdict: "degraded",
      blocked_count: 2,
      in_flight_count: 0,
      findings: [
        {
          finding_id: "task-hidden",
          kind: "build_task",
          severity: "critical",
          description: "Hidden repo finding",
          workspace_id: "task-hidden",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          in_flight: false,
          engine_key: "sprocket-co/sprocket",
        },
        {
          finding_id: "task-kept",
          kind: "build_task",
          severity: "critical",
          description: "Kept repo finding",
          workspace_id: "task-kept",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          in_flight: false,
          engine_key: "sprocket-co/kept",
        },
      ],
      computed_at: new Date().toISOString(),
    };
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: state })),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("finding-row-task-kept")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("finding-row-task-hidden")).not.toBeInTheDocument();
  });

  it("shows the empty state once every remaining finding's repo is hidden", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            { repo: { owner: "DejvMatikar", repo: "alan" } },
            { repo: { owner: "sprocket-co", repo: "sprocket" }, hidden: true },
          ],
        }),
      ),
    );
    const state: HealthState = {
      verdict: "degraded",
      blocked_count: 1,
      in_flight_count: 0,
      findings: [
        {
          finding_id: "task-only-hidden",
          kind: "build_task",
          severity: "critical",
          description: "Only-hidden finding",
          workspace_id: "task-only-hidden",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          in_flight: false,
          engine_key: "sprocket-co/sprocket",
        },
      ],
      computed_at: new Date().toISOString(),
    };
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: state })),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("health-view-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("finding-row-task-only-hidden")).not.toBeInTheDocument();
  });

  it("keeps a finding with no engine_key regardless of which repos are hidden", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            { repo: { owner: "DejvMatikar", repo: "alan" } },
            { repo: { owner: "sprocket-co", repo: "sprocket" }, hidden: true },
          ],
        }),
      ),
    );
    const state: HealthState = {
      verdict: "degraded",
      blocked_count: 1,
      in_flight_count: 0,
      findings: [
        {
          finding_id: "task-no-engine-hv",
          kind: "build_task",
          severity: "critical",
          description: "No-engine finding",
          workspace_id: "task-no-engine-hv",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          in_flight: false,
          engine_key: null,
        },
      ],
      computed_at: new Date().toISOString(),
    };
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: state })),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("finding-row-task-no-engine-hv")).toBeInTheDocument();
    });
  });
});

describe("HealthView — ?engine= scope filtering", () => {
  function twoRepoState(): HealthState {
    return {
      verdict: "degraded",
      blocked_count: 2,
      in_flight_count: 0,
      findings: [
        {
          finding_id: "task-scoped",
          kind: "build_task",
          severity: "critical",
          description: "Scoped repo finding",
          workspace_id: "task-scoped",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          in_flight: false,
          engine_key: "acme/widget",
        },
        {
          finding_id: "task-other",
          kind: "build_task",
          severity: "critical",
          description: "Other repo finding",
          workspace_id: "task-other",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          in_flight: false,
          engine_key: "other/repo",
        },
      ],
      computed_at: new Date().toISOString(),
    };
  }

  it("shows only the findings scoped to the ?engine= param", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: twoRepoState() })),
    );
    renderView("/health?engine=acme%2Fwidget");
    await waitFor(() => {
      expect(screen.getByTestId("finding-row-task-scoped")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("finding-row-task-other")).not.toBeInTheDocument();
  });

  it("renders a scope indicator with a link back to the unscoped view", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: twoRepoState() })),
    );
    renderView("/health?engine=acme%2Fwidget");
    const indicator = await screen.findByTestId("health-scope-indicator");
    expect(indicator).toHaveTextContent("acme/widget");
    expect(within(indicator).getByRole("link", { name: "Show all repositories" })).toHaveAttribute(
      "href",
      "/health",
    );
  });

  it("renders no scope indicator and every repo's findings with no ?engine= param", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: twoRepoState() })),
    );
    renderView("/health");
    await waitFor(() => {
      expect(screen.getByTestId("finding-row-task-scoped")).toBeInTheDocument();
      expect(screen.getByTestId("finding-row-task-other")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("health-scope-indicator")).not.toBeInTheDocument();
  });

  it("layers the ?engine= scope on top of the existing hidden-engine filter", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            { repo: { owner: "DejvMatikar", repo: "alan" } },
            { repo: { owner: "acme", repo: "widget" }, hidden: true },
          ],
        }),
      ),
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: twoRepoState() })),
    );
    renderView("/health?engine=acme%2Fwidget");
    await waitFor(() => {
      expect(screen.getByTestId("health-view-empty")).toBeInTheDocument();
    });
  });
});

describe("HealthView — repository comparison table", () => {
  it("renders one row per repository from GET /api/health/repos, worst-health-first", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(
          repoHealthResponse({
            computed_at: "2026-06-01T00:00:00Z",
            rows: [
              repoRow("a/quiet", { outstanding_findings: { value: 1, sample: 1, low_data: false } }),
              repoRow("b/loud", { outstanding_findings: { value: 9, sample: 3, low_data: false } }),
            ],
          }),
        ),
      ),
    );
    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("repo-health-row-b/loud")).toBeInTheDocument();
    });
    expect(screen.getByTestId("repo-health-row-a/quiet")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").map((r) => r.textContent ?? "");
    const loudIndex = rows.findIndex((t) => t.includes("b/loud"));
    const quietIndex = rows.findIndex((t) => t.includes("a/quiet"));
    expect(quietIndex).toBeGreaterThan(loudIndex);
  });

  it("hides rows once ?engine= scopes the page, showing the metrics panel instead", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(repoHealthResponse({ rows: [repoRow("acme/widget")] })),
      ),
    );
    renderView("/health?engine=acme%2Fwidget");
    await waitFor(() => {
      expect(screen.getByTestId("health-metrics-panel")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("repo-health-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("health-metrics-fleet-fallback")).not.toBeInTheDocument();
  });

  it("renders the recurring-processes detail once the health page is scoped to a repository", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(repoHealthResponse({ rows: [repoRow("acme/widget")] })),
      ),
    );
    renderView("/health?engine=acme%2Fwidget");
    expect(
      await screen.findByTestId("recurring-processes-panel-acme/widget"),
    ).toBeInTheDocument();
  });

  it("renders no recurring-processes detail on the unscoped health page", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(repoHealthResponse({ rows: [repoRow("acme/widget")] })),
      ),
    );
    renderView("/health");
    await waitFor(() => {
      expect(screen.getByTestId("repo-health-panel")).toBeInTheDocument();
    });
    expect(screen.queryByTestId(/recurring-processes-panel-/)).not.toBeInTheDocument();
  });

  it("renders the driver-loops panel on the unscoped health page", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/driver-loops", () => HttpResponse.json({ supervised: false, loops: [] })),
    );
    renderView("/health");
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-panel")).toBeInTheDocument();
    });
  });

  it("renders no driver-loops panel once ?engine= scopes the health page", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(repoHealthResponse({ rows: [repoRow("acme/widget")] })),
      ),
      http.get("/api/driver-loops", () => HttpResponse.json({ supervised: false, loops: [] })),
    );
    renderView("/health?engine=acme%2Fwidget");
    await waitFor(() => {
      expect(screen.getByTestId("health-metrics-panel")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("driver-loops-panel")).not.toBeInTheDocument();
  });

  it("shows the fleet-fallback notice when the scoped repo has no matching row yet", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(repoHealthResponse({ rows: [repoRow("other/repo")] })),
      ),
    );
    renderView("/health?engine=acme%2Fwidget");
    await waitFor(() => {
      expect(screen.getByTestId("health-metrics-fleet-fallback")).toBeInTheDocument();
    });
  });

  it("shows the oldest of the findings and metrics timestamps as the table's freshness", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(
          repoHealthResponse({
            computed_at: "2026-06-01T00:00:00Z",
            metrics_computed_at: "2026-06-02T00:00:00Z",
            rows: [repoRow("acme/widget")],
          }),
        ),
      ),
    );
    renderView();
    const freshness = await screen.findByTestId("repo-health-freshness");
    expect(freshness).toHaveAttribute("title", new Date("2026-06-01T00:00:00Z").toLocaleString());
  });

  it("renders the fleet footer from the fleet windows in the repos response", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(
          repoHealthResponse({
            fleet: scopeWindows({
              outcome: metricWindows(healthWindow(0.9, 40), healthWindow(0.6, 90), "better"),
            }),
            rows: [repoRow("acme/widget")],
          }),
        ),
      ),
    );
    renderView();
    expect(await screen.findByTestId("repo-health-footer-outcome-value")).toHaveTextContent("60%");
    expect(screen.getByTestId("repo-health-footer-outcome-value-symbol")).toHaveTextContent("▲");
    expect(screen.getByTestId("repo-health-legend")).toBeInTheDocument();
  });

  it("shows the scoped repository's windows with its in-flight count and attribution note", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () =>
        HttpResponse.json(
          repoHealthResponse({
            unattributed: 2,
            rows: [
              repoRow("acme/widget", {
                in_flight: { value: 5, sample: 5, low_data: false },
                windows: scopeWindows({
                  build_success: metricWindows(healthWindow(0.75, 20), healthWindow(0.5, 60), "better"),
                }),
              }),
            ],
          }),
        ),
      ),
    );
    renderView("/health?engine=acme%2Fwidget");
    expect(await screen.findByTestId("repo-metric-build_success-7d")).toHaveTextContent("75%");
    expect(screen.getByTestId("health-metrics-in-flight")).toHaveTextContent("Right now: 5 in flight");
    expect(screen.getByTestId("health-metrics-attribution-note")).toHaveTextContent("2 records");
    expect(screen.getByTestId("health-freshness")).toBeInTheDocument();
  });

  it("shows an error in the scoped metrics panel when the repos request fails", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/repos", () => HttpResponse.error()),
    );
    renderView("/health?engine=acme%2Fwidget");
    expect(await screen.findByTestId("health-metrics-error")).toBeInTheDocument();
  });
});

describe("HealthView — unblocker activity and findings disclosure", () => {
  function serveDegraded() {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: _makeDegradedState() }),
      ),
    );
  }

  it("mounts the unblocker activity panel on the fleet view unscoped", async () => {
    let engineParam: string | null = "unset";
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/unblocker-activity", ({ request }) => {
        engineParam = new URL(request.url).searchParams.get("engine");
        return HttpResponse.json(emptyActivityPayload(engineParam));
      }),
    );
    renderView();
    await screen.findByTestId("unblocker-activity-panel");
    await screen.findByTestId("unblocker-activity-empty-window");
    expect(engineParam).toBeNull();
  });

  it("orders the fleet view as repository health, driver loops, unblocker activity, then other findings", async () => {
    serveDegraded();
    renderView();
    const disclosure = await screen.findByTestId("health-findings-disclosure");
    const table = screen.getByTestId("repo-health-panel");
    const loops = await screen.findByTestId("driver-loops-panel");
    const panel = screen.getByTestId("unblocker-activity-panel");
    expect(table.compareDocumentPosition(loops) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(loops.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(panel.compareDocumentPosition(disclosure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("mounts the panel on the scoped view for that repository, after the scope indicator", async () => {
    let engineParam: string | null = null;
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: null })),
      http.get("/api/health/unblocker-activity", ({ request }) => {
        engineParam = new URL(request.url).searchParams.get("engine");
        return HttpResponse.json(emptyActivityPayload(engineParam));
      }),
    );
    renderView("/health?engine=acme%2Fwidget");
    await screen.findByTestId("unblocker-activity-empty-repo-window");
    const indicator = screen.getByTestId("health-scope-indicator");
    const panel = screen.getByTestId("unblocker-activity-panel");
    expect(indicator.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(engineParam).toBe("acme/widget");
  });

  it("orders the scoped view as health metrics, recurring processes, unblocker activity, then other findings", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          health_state: {
            ..._makeDegradedState(),
            findings: [
              {
                finding_id: "task-scoped",
                kind: "build_task",
                severity: "critical",
                description: "Scoped repo finding",
                workspace_id: "task-scoped",
                pr_number: null,
                blocker_flags: [],
                action: "deep_link",
                in_flight: false,
                engine_key: "acme/widget",
              },
            ],
          },
        }),
      ),
    );
    renderView("/health?engine=acme%2Fwidget");
    const disclosure = await screen.findByTestId("health-findings-disclosure");
    const metrics = await screen.findByTestId("health-metrics-panel");
    const recurring = await screen.findByTestId("recurring-processes-panel-acme/widget");
    const panel = screen.getByTestId("unblocker-activity-panel");
    expect(metrics.compareDocumentPosition(recurring) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(recurring.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(panel.compareDocumentPosition(disclosure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(panel.nextElementSibling).toBe(disclosure);
  });

  it("arrives with the unblocker disclosure collapsed and expands it on click", async () => {
    server.use(
      http.get("/api/health/unblocker-activity", () => HttpResponse.json(activePayload())),
    );
    renderView("/health?engine=acme%2Fwidget");
    const disclosure = await screen.findByTestId("unblocker-activity-disclosure");
    await expectDisclosureState(disclosure, false);
    activateDisclosure(disclosure, "click");
    await expectDisclosureState(disclosure, true);
    expect(within(disclosure).getByTestId("unblocker-aggregates-table")).toBeVisible();
    expect(within(disclosure).getByTestId("unblocker-workspaces-table")).toBeVisible();
  });

  it("keeps the findings table inside a closed disclosure by default, still in the DOM", async () => {
    serveDegraded();
    renderView();
    const disclosure = await screen.findByTestId("health-findings-disclosure");
    expect(disclosure).not.toHaveAttribute("open");
    expect(disclosure.querySelector("summary")).toHaveTextContent("Other health findings (2)");
    expect(within(disclosure).getByTestId("health-findings-table")).toBeInTheDocument();
  });

  it("places the unblocker panel above the collapsed findings", async () => {
    serveDegraded();
    renderView();
    const disclosure = await screen.findByTestId("health-findings-disclosure");
    const panel = screen.getByTestId("unblocker-activity-panel");
    expect(panel.compareDocumentPosition(disclosure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows the closed marker on the findings disclosure and opens it by click, Enter and Space", async () => {
    serveDegraded();
    renderView();
    const disclosure = await screen.findByTestId("health-findings-disclosure");
    await expectDisclosureState(disclosure, false);
    await expectTogglesOnEveryActivation(disclosure);
  });

  it("renders no disposition column, selector or submit control anywhere", async () => {
    serveDegraded();
    renderView();
    await screen.findByTestId("health-findings-table");
    expect(screen.queryByRole("columnheader", { name: "Disposition" })).not.toBeInTheDocument();
    expect(screen.queryByTestId(/^finding-disposition-/)).not.toBeInTheDocument();
    expect(screen.queryByTestId(/^disposition-/)).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("renders no findings disclosure when there are no findings to show", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: _makeHealthyState() }),
      ),
    );
    renderView();
    await screen.findByTestId("health-view-empty");
    expect(screen.queryByTestId("health-findings-disclosure")).not.toBeInTheDocument();
  });
});
