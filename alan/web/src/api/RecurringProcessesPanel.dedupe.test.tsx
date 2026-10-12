import { act, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { RecurringStatus, RepoStatus, UnblockScopedActivity } from "../api";
import RepoStatusStrip from "../features/home/components/RepoStatusStrip";
import { ServerEventsContext, type ServerEventsValue } from "./ServerEventsProvider";
import { server } from "../test/server";
import RecurringProcessesPanel from "../features/health/components/RecurringProcessesPanel";

const KEY = "acme/widget";
const FRESH = { status: "fresh", detail: "" } as const;

const REPO_STATUS: RepoStatus = {
  slug: KEY,
  is_default: false,
  main_ci: { provenance: FRESH, green_count: 3, red_count: 0, counted_over: "main_ci_workflow" },
  open_prs: { provenance: FRESH, total: 4, authored_by_alan: 2 },
  cost: { provenance: FRESH, total_cost_usd: 15, unknown_count: 0 },
  delivery_time: { provenance: FRESH, by_size: [] },
  last_deploy: {
    provenance: { status: "unknown", detail: "" },
    outcome: null,
    pr_number: null,
    at: null,
  },
  health: { provenance: FRESH, finding_count: 2, verdict: null },
};

const RECURRING_STATUS: RecurringStatus = {
  slug: KEY,
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

const QUIET_ACTIVITY: UnblockScopedActivity = {
  scope: "repo",
  repo: KEY,
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

interface Tally {
  status: number;
  recurring: number;
}

function serveCounted(): Tally {
  const tally: Tally = { status: 0, recurring: 0 };
  server.use(
    http.get(`/api/engines/${KEY}/status`, () => {
      tally.status += 1;
      return HttpResponse.json(REPO_STATUS);
    }),
    http.get(`/api/engines/${KEY}/recurring-status`, () => {
      tally.recurring += 1;
      return HttpResponse.json(RECURRING_STATUS);
    }),
    http.get("/api/health/unblocker-activity", () => HttpResponse.json(QUIET_ACTIVITY)),
  );
  return tally;
}

function eventsValue(engines: number): ServerEventsValue {
  return { counters: { workspace_list: 0, health: 0, engines }, connected: true };
}

function bothConsumers(engines: number) {
  return (
    <MemoryRouter>
      <ServerEventsContext.Provider value={eventsValue(engines)}>
        <RepoStatusStrip engineKey={KEY} />
        <RecurringProcessesPanel engineKey={KEY} />
      </ServerEventsContext.Provider>
    </MemoryRouter>
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 25);
    });
  });
}

async function renderSettled() {
  const view = render(bothConsumers(0));
  await screen.findByTestId(`recurring-processes-panel-${KEY}`);
  await screen.findByTestId(`repo-status-group-${KEY}-recurring-total`);
  await settle();
  return view;
}

let tally: Tally;

beforeEach(() => {
  tally = serveCounted();
});

describe("RecurringProcessesPanel beside RepoStatusStrip — shared recurring-status read", () => {
  it("issues exactly one recurring-status request for both consumers", async () => {
    await renderSettled();
    expect(tally.recurring).toBe(1);
  });

  it("leaves the repo-status request count at one, independent of the recurring read", async () => {
    await renderSettled();
    expect(tally.status).toBe(1);
  });

  it("issues exactly one refetch per invalidation for both consumers", async () => {
    const view = await renderSettled();
    view.rerender(bothConsumers(1));
    await waitFor(() => {
      expect(tally.recurring).toBe(2);
    });
    await settle();
    expect(tally.recurring).toBe(2);
    expect(tally.status).toBe(2);
  });

  it("keeps both consumers rendering the shared payload after the refetch", async () => {
    const view = await renderSettled();
    view.rerender(bothConsumers(1));
    await settle();
    expect(screen.getByTestId(`recurring-processes-panel-${KEY}`)).toHaveAttribute("aria-busy", "false");
    expect(screen.getByTestId(`repo-status-group-${KEY}-recurring-total`)).toBeInTheDocument();
  });
});
