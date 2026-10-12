import { act, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { RecurringStatus, RepoStatus, UnblockScopedActivity } from "../../../api";
import { ServerEventsContext, type ServerEventsValue } from "../../../api/ServerEventsProvider";
import { server } from "../../../test/server";
import RepoStatusStrip from "./RepoStatusStrip";

const FRESH = { status: "fresh", detail: "" } as const;
const WIDGET = "acme/widget";
const OTHER = "acme/other";

function repoStatus(slug: string, costUsd: number): RepoStatus {
  return {
    slug,
    is_default: false,
    main_ci: { provenance: FRESH, green_count: 3, red_count: 0, counted_over: "main_ci_workflow" },
    open_prs: { provenance: FRESH, total: 4, authored_by_alan: 2 },
    cost: { provenance: FRESH, total_cost_usd: costUsd, unknown_count: 0 },
    delivery_time: { provenance: FRESH, by_size: [] },
    last_deploy: {
      provenance: { status: "unknown", detail: "" },
      outcome: null,
      pr_number: null,
      at: null,
    },
    health: { provenance: FRESH, finding_count: 2, verdict: null },
  };
}

function recurringStatus(slug: string): RecurringStatus {
  return {
    slug,
    is_default: false,
    processes: [],
    service_scope_note: null,
    backup_coverage: {
      provenance: FRESH,
      included_in_last_snapshot: true,
      changed_since_count: 0,
      excluded_caveat: "per-repository engine configuration is structurally excluded",
    },
  };
}

function quietActivity(): UnblockScopedActivity {
  return {
    scope: "repo",
    repo: WIDGET,
    window_days: 30,
    events_total: 0,
    workspaces_touched: 0,
    waited_workspaces: 0,
    diagnosed_workspaces: 0,
    diagnosable_workspaces: 0,
    still_stuck_workspaces: 0,
    gave_up_workspaces: 0,
    aggregates: [],
    fleet_ever_recorded: true,
    events: [],
    workspaces: [],
  };
}

function eventsValue(engines: number): ServerEventsValue {
  return { counters: { workspace_list: 0, health: 0, engines }, connected: true };
}

interface Gate {
  release: () => void;
}

function armGate(): { gate: Gate; held: Promise<void> } {
  const gate: Gate = { release: () => undefined };
  const held = new Promise<void>((resolve) => {
    gate.release = () => {
      resolve();
    };
  });
  return { gate, held };
}

function serveRecurring(slug: string): void {
  server.use(
    http.get(`/api/engines/${slug}/recurring-status`, () => HttpResponse.json(recurringStatus(slug))),
  );
}

function serveStatusThenHeld(slug: string, first: RepoStatus, second: RepoStatus | "error"): Gate {
  const { gate, held } = armGate();
  let calls = 0;
  server.use(
    http.get(`/api/engines/${slug}/status`, async () => {
      calls += 1;
      if (calls === 1) return HttpResponse.json(first);
      await held;
      return second === "error"
        ? HttpResponse.json({ detail: "boom" }, { status: 500 })
        : HttpResponse.json(second);
    }),
  );
  return gate;
}

function renderStrip(engineKey: string) {
  const tree = (key: string, engines: number) => (
    <MemoryRouter>
      <ServerEventsContext.Provider value={eventsValue(engines)}>
        <RepoStatusStrip engineKey={key} />
      </ServerEventsContext.Provider>
    </MemoryRouter>
  );
  const view = render(tree(engineKey, 0));
  return {
    bumpEngines: () => {
      view.rerender(tree(engineKey, 1));
    },
    switchTo: (key: string) => {
      view.rerender(tree(key, 0));
    },
  };
}

function expectLoadingTileAndBusyStripExclusive(): void {
  const busy = document.querySelector('[aria-busy="true"]');
  const loading = document.querySelector('[data-testid^="repo-status-loading-"]');
  expect(busy !== null && loading !== null).toBe(false);
}

function stripOf(slug: string): HTMLElement {
  return screen.getByTestId(`repo-status-strip-${slug}`);
}

function costChipOf(slug: string): HTMLElement {
  return screen.getByTestId(`aggregate-chip-${slug}-cost`);
}

beforeEach(() => {
  server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.json(quietActivity())));
  serveRecurring(WIDGET);
  serveRecurring(OTHER);
});

describe("RepoStatusStrip — busy indication", () => {
  it("reports not busy at rest", async () => {
    server.use(
      http.get(`/api/engines/${WIDGET}/status`, () => HttpResponse.json(repoStatus(WIDGET, 15))),
    );
    renderStrip(WIDGET);
    await screen.findByTestId(`repo-status-strip-${WIDGET}`);
    expect(stripOf(WIDGET)).toHaveAttribute("aria-busy", "false");
    expect(screen.queryByTestId(`repo-status-refreshing-${WIDGET}`)).not.toBeInTheDocument();
    expectLoadingTileAndBusyStripExclusive();
  });

  it("reports busy during a same-repository refresh with the prior counts visible and no loading tile", async () => {
    const gate = serveStatusThenHeld(WIDGET, repoStatus(WIDGET, 15), repoStatus(WIDGET, 16));
    const view = renderStrip(WIDGET);
    await screen.findByTestId(`repo-status-strip-${WIDGET}`);

    view.bumpEngines();
    await waitFor(() => {
      expect(stripOf(WIDGET)).toHaveAttribute("aria-busy", "true");
    });
    expect(screen.getByTestId(`repo-status-refreshing-${WIDGET}`)).toBeInTheDocument();
    expect(costChipOf(WIDGET)).toHaveTextContent("$15.00");
    expect(screen.queryByTestId(`repo-status-loading-${WIDGET}`)).not.toBeInTheDocument();
    expectLoadingTileAndBusyStripExclusive();

    act(() => {
      gate.release();
    });
    await waitFor(() => {
      expect(costChipOf(WIDGET)).toHaveTextContent("$16.00");
    });
    expect(stripOf(WIDGET)).toHaveAttribute("aria-busy", "false");
  });

  it("enters the loading arm on a repository switch, showing neither the previous counts nor a busy strip", async () => {
    server.use(
      http.get(`/api/engines/${WIDGET}/status`, () => HttpResponse.json(repoStatus(WIDGET, 15))),
    );
    const { gate, held } = armGate();
    server.use(
      http.get(`/api/engines/${OTHER}/status`, async () => {
        await held;
        return HttpResponse.json(repoStatus(OTHER, 27));
      }),
    );
    const view = renderStrip(WIDGET);
    await screen.findByTestId(`repo-status-strip-${WIDGET}`);
    expect(costChipOf(WIDGET)).toHaveTextContent("$15.00");

    view.switchTo(OTHER);
    expect(screen.getByTestId(`repo-status-loading-${OTHER}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`repo-status-strip-${WIDGET}`)).not.toBeInTheDocument();
    expect(screen.queryByText("$15.00")).not.toBeInTheDocument();
    expectLoadingTileAndBusyStripExclusive();

    act(() => {
      gate.release();
    });
    await screen.findByTestId(`repo-status-strip-${OTHER}`);
    expect(costChipOf(OTHER)).toHaveTextContent("$27.00");
    expect(stripOf(OTHER)).toHaveAttribute("aria-busy", "false");
    expectLoadingTileAndBusyStripExclusive();
  });

  it("shows the refresh-failed line with the values retained and not busy when a same-repository refresh fails", async () => {
    const gate = serveStatusThenHeld(WIDGET, repoStatus(WIDGET, 15), "error");
    const view = renderStrip(WIDGET);
    await screen.findByTestId(`repo-status-strip-${WIDGET}`);

    view.bumpEngines();
    await waitFor(() => {
      expect(stripOf(WIDGET)).toHaveAttribute("aria-busy", "true");
    });
    act(() => {
      gate.release();
    });
    await screen.findByTestId(`repo-status-refresh-error-${WIDGET}`);
    expect(stripOf(WIDGET)).toHaveAttribute("aria-busy", "false");
    expect(screen.queryByTestId(`repo-status-refreshing-${WIDGET}`)).not.toBeInTheDocument();
    expect(costChipOf(WIDGET)).toHaveTextContent("$15.00");
    expectLoadingTileAndBusyStripExclusive();
  });
});
