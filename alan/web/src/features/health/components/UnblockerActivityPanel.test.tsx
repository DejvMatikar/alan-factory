import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type {
  UnblockActivityEvent,
  UnblockActivityResponse,
  UnblockFleetActivity,
  UnblockScopedActivity,
  UnblockShapeAggregate,
} from "../../../api";
import { ServerEventsContext } from "../../../api/ServerEventsProvider";
import { server } from "../../../test/server";
import UnblockerActivityPanel from "./UnblockerActivityPanel";

type WorkspaceSource = NonNullable<UnblockScopedActivity["workspaces"]>[number];

function aggregate(overrides: Partial<UnblockShapeAggregate> = {}): UnblockShapeAggregate {
  return {
    shape: "merge_park",
    total: 6,
    workspaces: 4,
    cleared_workspaces: 2,
    still_stuck_workspaces: 1,
    gone_workspaces: 1,
    retried_workspaces: 1,
    attempt_outcomes: [
      { outcome: "acted", count: 3 },
      { outcome: "declined", count: 1 },
      { outcome: "failed", count: 1 },
    ],
    report_outcomes: [{ outcome: "needs_human", count: 1 }],
    ...overrides,
  };
}

function fleet(overrides: Partial<UnblockFleetActivity> = {}): UnblockFleetActivity {
  return {
    scope: "fleet",
    window_days: 30,
    events_total: 6,
    workspaces_touched: 4,
    waited_workspaces: 2,
    diagnosed_workspaces: 1,
    diagnosable_workspaces: 3,
    still_stuck_workspaces: 1,
    gave_up_workspaces: 1,
    aggregates: [aggregate()],
    ever_recorded: true,
    unattributed_events: 0,
    ...overrides,
  };
}

function workspace(overrides: Partial<WorkspaceSource> = {}): WorkspaceSource {
  return {
    workspace_key: "ws-alpha",
    last_at: "2026-09-19T14:05Z",
    shape: "merge_park",
    events_total: 1,
    events_listed: 1,
    attempts_total: 1,
    attempts_ceiling: 5,
    clearance: "cleared",
    gave_up: false,
    issue_url: null,
    diagnosis: null,
    ...overrides,
  };
}

function event(overrides: Partial<UnblockActivityEvent> = {}): UnblockActivityEvent {
  return {
    id: 1,
    at: "2026-09-19T14:05Z",
    workspace_key: "ws-alpha",
    shape: "merge_park",
    kind: "attempt",
    action: "rebase_branch",
    outcome: "acted",
    reason: null,
    authority_holder: null,
    clearance: "cleared",
    attempt: {
      fingerprint_index: 1,
      fingerprint_ceiling: 2,
      workspace_index: 1,
      workspace_ceiling: 3,
    },
    attempts_total: null,
    issue_url: null,
    diagnosis: null,
    ...overrides,
  };
}

function repo(overrides: Partial<UnblockScopedActivity> = {}): UnblockScopedActivity {
  return {
    scope: "repo",
    repo: "acme/widget",
    window_days: 30,
    events_total: 1,
    workspaces_touched: 1,
    waited_workspaces: 0,
    diagnosed_workspaces: 0,
    diagnosable_workspaces: 1,
    still_stuck_workspaces: 0,
    gave_up_workspaces: 0,
    aggregates: [aggregate({ shape: "repo_shape" })],
    fleet_ever_recorded: true,
    events: [event()],
    workspaces: [workspace()],
    ...overrides,
  };
}

function renderPanel(engine: string | null, payload: UnblockActivityResponse) {
  server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.json(payload)));
  return render(
    <MemoryRouter>
      <UnblockerActivityPanel engine={engine} />
    </MemoryRouter>,
  );
}

describe("UnblockerActivityPanel — states", () => {
  it("renders the h2 heading and a busy loading state before the response arrives", () => {
    renderPanel(null, fleet());
    expect(screen.getByRole("heading", { level: 2, name: "Unblocker activity" })).toBeInTheDocument();
    expect(screen.getByTestId("unblocker-activity-panel")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByTestId("unblocker-activity-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-activity-disclosure")).not.toBeInTheDocument();
  });

  it("clears the busy flag and the loading text once data has loaded", async () => {
    renderPanel(null, fleet());
    await screen.findByTestId("unblocker-activity-summary");
    expect(screen.getByTestId("unblocker-activity-panel")).toHaveAttribute("aria-busy", "false");
    expect(screen.queryByTestId("unblocker-activity-loading")).not.toBeInTheDocument();
  });

  it("shows a distinct alert when the fetch fails and none of the empty states", async () => {
    server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.error()));
    render(
      <MemoryRouter>
        <UnblockerActivityPanel engine={null} />
      </MemoryRouter>,
    );
    const alert = await screen.findByTestId("unblocker-activity-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("could not be loaded");
    expect(screen.queryByTestId("unblocker-activity-empty-window")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-activity-empty-never")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-activity-empty-repo-window")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-activity-disclosure")).not.toBeInTheDocument();
  });

  it("keeps the last loaded activity on screen with a stale notice when a refetch fails", async () => {
    let failing = false;
    server.use(
      http.get("/api/health/unblocker-activity", () =>
        failing ? HttpResponse.error() : HttpResponse.json(fleet()),
      ),
    );
    const view = (health: number) => (
      <ServerEventsContext.Provider
        value={{ counters: { workspace_list: 0, health, engines: 0 }, connected: true }}
      >
        <MemoryRouter>
          <UnblockerActivityPanel engine={null} />
        </MemoryRouter>
      </ServerEventsContext.Provider>
    );
    const { rerender } = render(view(0));
    await screen.findByTestId("unblocker-activity-summary");
    failing = true;
    rerender(view(1));
    const stale = await screen.findByTestId("unblocker-activity-stale");
    expect(stale).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("unblocker-activity-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-activity-error")).not.toBeInTheDocument();
  });

  it("says nothing happened in the window when the fleet has history but no activity", async () => {
    renderPanel(
      null,
      fleet({ events_total: 0, waited_workspaces: 0, workspaces_touched: 0, aggregates: [] }),
    );
    const notice = await screen.findByTestId("unblocker-activity-empty-window");
    expect(notice).toHaveTextContent("Nothing happened in the last 30 days");
    expect(screen.queryByTestId("unblocker-activity-empty-never")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-activity-summary")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-activity-disclosure")).not.toBeInTheDocument();
  });

  it("says the unblocker never recorded anything when the fleet has no history at all", async () => {
    renderPanel(
      null,
      fleet({
        ever_recorded: false,
        events_total: 0,
        waited_workspaces: 0,
        workspaces_touched: 0,
        aggregates: [],
      }),
    );
    const notice = await screen.findByTestId("unblocker-activity-empty-never");
    expect(notice).toHaveTextContent("The unblocker has never recorded any activity");
    expect(screen.queryByTestId("unblocker-activity-empty-window")).not.toBeInTheDocument();
  });

  it("says a repo had no activity while the fleet did elsewhere", async () => {
    renderPanel(
      "acme/widget",
      repo({
        events_total: 0,
        waited_workspaces: 0,
        workspaces_touched: 0,
        aggregates: [],
        events: [],
        workspaces: [],
      }),
    );
    const notice = await screen.findByTestId("unblocker-activity-empty-repo-window");
    expect(notice).toHaveTextContent(
      "No unblocker activity for acme/widget in the last 30 days (the fleet has recorded activity elsewhere)",
    );
    expect(screen.queryByTestId("unblocker-activity-empty-never")).not.toBeInTheDocument();
  });

  it("says never recorded, not a repo-window message, when a repo view has no fleet history", async () => {
    renderPanel(
      "acme/widget",
      repo({
        fleet_ever_recorded: false,
        events_total: 0,
        waited_workspaces: 0,
        workspaces_touched: 0,
        aggregates: [],
        events: [],
        workspaces: [],
      }),
    );
    expect(await screen.findByTestId("unblocker-activity-empty-never")).toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-activity-empty-repo-window")).not.toBeInTheDocument();
  });

  it("shows the summary instead of an empty state when the window holds only waiting", async () => {
    renderPanel(
      null,
      fleet({
        events_total: 0,
        workspaces_touched: 0,
        waited_workspaces: 2,
        diagnosed_workspaces: 0,
        diagnosable_workspaces: 0,
        aggregates: [],
      }),
    );
    expect(await screen.findByTestId("unblocker-metric-waited")).toHaveTextContent("2");
    expect(screen.queryByTestId("unblocker-activity-empty-window")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-aggregates")).not.toBeInTheDocument();
  });
});

describe("UnblockerActivityPanel — fleet view", () => {
  it("states the window in the subtitle and labels the scope as all repositories", async () => {
    renderPanel(null, fleet({ window_days: 30 }));
    const subtitle = await screen.findByTestId("unblocker-activity-subtitle");
    expect(subtitle).toHaveTextContent("last 30 days");
    expect(subtitle).toHaveTextContent("all repositories");
  });

  it("renders the four summary figures including the diagnosis coverage sentence", async () => {
    renderPanel(null, fleet());
    expect(await screen.findByTestId("unblocker-metric-events")).toHaveTextContent("6");
    expect(screen.getByTestId("unblocker-metric-touched")).toHaveTextContent("4");
    expect(screen.getByTestId("unblocker-metric-waited")).toHaveTextContent("2");
    expect(screen.getByText("Waiting only — host has not acted or given up")).toBeInTheDocument();
    expect(screen.getByTestId("unblocker-metric-diagnosis")).toHaveTextContent("1 of 3");
    expect(screen.getByText("1 of 3 rescued workspaces had a diagnosis.")).toBeInTheDocument();
  });

  it("renders the park kinds as one table row per kind with its shape, total and workspace count", async () => {
    renderPanel(
      null,
      fleet({ aggregates: [aggregate(), aggregate({ shape: "question_park", total: 1, workspaces: 1 })] }),
    );
    const table = await screen.findByTestId("unblocker-aggregates-table");
    expect(within(table).getAllByRole("rowheader").map((h) => h.textContent)).toEqual([
      "merge_park",
      "question_park",
    ]);
    const cells = within(screen.getByTestId("unblocker-aggregate-question_park")).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("1");
    expect(cells[1]).toHaveTextContent("1");
  });

  it("names all repositories in the park-kinds heading", async () => {
    renderPanel(null, fleet());
    expect(
      await screen.findByRole("heading", { level: 3, name: "Park kinds — all repositories" }),
    ).toBeInTheDocument();
  });

  it("explains that cleared is not proof the unblocker's action worked", async () => {
    renderPanel(null, fleet());
    expect(await screen.findByTestId("unblocker-clearance-note")).toHaveTextContent(
      "not proof that the unblocker's action worked",
    );
  });

  it("renders no workspaces table and states why the per-workspace view is unavailable", async () => {
    renderPanel(null, fleet());
    await screen.findByTestId("unblocker-aggregates");
    expect(screen.queryByTestId("unblocker-workspaces")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-workspaces-table")).not.toBeInTheDocument();
    const note = screen.getByTestId("unblocker-workspaces-unavailable");
    expect(note).toHaveTextContent("one repository at a time");
    expect(note).toHaveTextContent("not available across all repositories");
  });

  it("states why the per-workspace view is unavailable even when the window holds only waiting", async () => {
    renderPanel(
      null,
      fleet({
        events_total: 0,
        workspaces_touched: 0,
        waited_workspaces: 2,
        diagnosed_workspaces: 0,
        diagnosable_workspaces: 0,
        aggregates: [],
      }),
    );
    expect(await screen.findByTestId("unblocker-metric-waited")).toHaveTextContent("2");
    expect(screen.getByTestId("unblocker-workspaces-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-aggregates")).not.toBeInTheDocument();
  });

  it("never renders a workspace link or an issue link", async () => {
    renderPanel(null, fleet());
    await screen.findByTestId("unblocker-aggregates");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("reports events that could not be attributed to a repository when there are some", async () => {
    renderPanel(null, fleet({ unattributed_events: 3 }));
    expect(await screen.findByTestId("unblocker-unattributed")).toHaveTextContent(
      "3 events could not be attributed to a repository.",
    );
  });

  it("omits the unattributed line when every event is attributed", async () => {
    renderPanel(null, fleet({ unattributed_events: 0 }));
    await screen.findByTestId("unblocker-aggregates");
    expect(screen.queryByTestId("unblocker-unattributed")).not.toBeInTheDocument();
  });
});

describe("UnblockerActivityPanel — repository view", () => {
  it("names the repository in the subtitle and the park-kind heading", async () => {
    renderPanel("acme/widget", repo());
    expect(await screen.findByTestId("unblocker-activity-subtitle")).toHaveTextContent("acme/widget");
    expect(screen.getByRole("heading", { level: 3, name: "Park kinds — acme/widget" })).toBeInTheDocument();
    expect(screen.queryByText(/all repositories/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-unattributed")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-workspaces-unavailable")).not.toBeInTheDocument();
  });

  it("renders the park-kinds table and the per-workspace table together", async () => {
    renderPanel("acme/widget", repo());
    expect(await screen.findByTestId("unblocker-aggregates-table")).toBeInTheDocument();
    expect(screen.getByTestId("unblocker-workspaces-table")).toBeInTheDocument();
  });

  it("renders one workspace row per touched workspace with its events as child rows", async () => {
    renderPanel(
      "acme/widget",
      repo({
        events_total: 4,
        workspaces_touched: 2,
        workspaces: [
          workspace({ workspace_key: "ws-a", events_total: 2 }),
          workspace({ workspace_key: "ws-b", events_total: 2 }),
        ],
        events: [
          event({ id: 4, workspace_key: "ws-b" }),
          event({ id: 3, workspace_key: "ws-a" }),
          event({ id: 2, workspace_key: "ws-b" }),
          event({ id: 1, workspace_key: "ws-a" }),
        ],
      }),
    );
    const table = await screen.findByTestId("unblocker-workspaces-table");
    expect(within(table).getAllByRole("rowheader")).toHaveLength(2);
    const groupA = screen.getByTestId("unblocker-workspace-group-ws-a");
    expect(within(groupA).getByTestId("unblocker-event-3")).toBeInTheDocument();
    expect(within(groupA).getByTestId("unblocker-event-1")).toBeInTheDocument();
    expect(within(groupA).queryByTestId("unblocker-event-4")).not.toBeInTheDocument();
  });

  it("links each workspace to its workspace page", async () => {
    renderPanel("acme/widget", repo());
    const link = await screen.findByRole("link", { name: "ws-alpha" });
    expect(link).toHaveAttribute("href", "/workspaces/ws-alpha");
  });

  it("shows the panel-level cap line when fewer events are listed than the uncapped total", async () => {
    renderPanel("acme/widget", repo({ events_total: 73 }));
    expect(await screen.findByTestId("unblocker-events-cap")).toHaveTextContent("latest 1 of 73");
  });

  it("omits the cap line when every event is listed", async () => {
    renderPanel("acme/widget", repo({ events_total: 1 }));
    await screen.findByTestId("unblocker-workspaces");
    expect(screen.queryByTestId("unblocker-events-cap")).not.toBeInTheDocument();
  });

  it("omits the workspaces section when the repo only has waiting workspaces", async () => {
    renderPanel(
      "acme/widget",
      repo({
        events_total: 0,
        waited_workspaces: 1,
        workspaces_touched: 0,
        aggregates: [],
        events: [],
        workspaces: [],
      }),
    );
    await screen.findByTestId("unblocker-metric-waited");
    expect(screen.queryByTestId("unblocker-workspaces")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-workspaces-unavailable")).not.toBeInTheDocument();
  });

  it("offers only row toggles and links, never a form control", async () => {
    renderPanel(
      "acme/widget",
      repo({ workspaces: [workspace({ issue_url: "https://github.com/acme/widget/issues/12" })] }),
    );
    const panel = await screen.findByTestId("unblocker-activity-panel");
    await screen.findByTestId("unblocker-workspaces-table");
    expect(panel.querySelector("input, select, textarea, form")).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getAllByRole("link")).toHaveLength(2);
  });
});
