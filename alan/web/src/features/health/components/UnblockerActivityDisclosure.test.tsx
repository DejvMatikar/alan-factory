import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type {
  UnblockActivityResponse,
  UnblockFleetActivity,
  UnblockScopedActivity,
  UnblockShapeAggregate,
} from "../../../api";
import { ServerEventsContext } from "../../../api/ServerEventsProvider";
import {
  activateDisclosure,
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { server } from "../../../test/server";
import UnblockerActivityPanel from "./UnblockerActivityPanel";

const AGGREGATE: UnblockShapeAggregate = {
  shape: "merge_park",
  total: 6,
  workspaces: 4,
  cleared_workspaces: 2,
  still_stuck_workspaces: 1,
  gone_workspaces: 1,
  retried_workspaces: 1,
  attempt_outcomes: [{ outcome: "acted", count: 3 }],
  report_outcomes: [{ outcome: "needs_human", count: 1 }],
};

function fleet(overrides: Partial<UnblockFleetActivity> = {}): UnblockFleetActivity {
  return {
    scope: "fleet",
    window_days: 30,
    events_total: 6,
    workspaces_touched: 4,
    waited_workspaces: 0,
    diagnosed_workspaces: 0,
    diagnosable_workspaces: 0,
    still_stuck_workspaces: 2,
    gave_up_workspaces: 1,
    aggregates: [AGGREGATE],
    ever_recorded: true,
    unattributed_events: 0,
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
    diagnosable_workspaces: 0,
    still_stuck_workspaces: 0,
    gave_up_workspaces: 0,
    aggregates: [AGGREGATE],
    fleet_ever_recorded: true,
    events: [
      {
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
        clearance: "cleared",
        gave_up: false,
        issue_url: null,
        diagnosis: null,
      },
    ],
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

async function disclosureOf(): Promise<HTMLElement> {
  return screen.findByTestId("unblocker-activity-disclosure");
}

describe("UnblockerActivityPanel — collapsible disclosure", () => {
  it("is collapsed on arrival, with the tables still in the DOM", async () => {
    renderPanel("acme/widget", repo());
    const disclosure = await disclosureOf();
    expect(disclosure).not.toHaveAttribute("open");
    expect(within(disclosure).getByTestId("unblocker-aggregates-table")).toBeInTheDocument();
    expect(within(disclosure).getByTestId("unblocker-workspaces-table")).toBeInTheDocument();
  });

  it("summarises the activity on the collapsed summary line", async () => {
    renderPanel(null, fleet());
    const disclosure = await disclosureOf();
    expect(disclosureSummary(disclosure)).toHaveTextContent(
      "Unblocker activity — 6 events · 4 workspaces touched · 2 still stuck · 1 gave up",
    );
  });

  it("leaves the alarming counts off the summary line when none are non-zero", async () => {
    renderPanel(null, fleet({ still_stuck_workspaces: 0, gave_up_workspaces: 0 }));
    const disclosure = await disclosureOf();
    const text = disclosureSummary(disclosure).textContent ?? "";
    expect(text).toContain("6 events · 4 workspaces touched");
    expect(text).not.toContain("still stuck");
    expect(text).not.toContain("gave up");
  });

  it("states the collapsed marker with a glyph hidden from assistive tech", async () => {
    renderPanel(null, fleet());
    await expectDisclosureState(await disclosureOf(), false);
  });

  it("gives the summary the standard minimum tap height", async () => {
    renderPanel(null, fleet());
    expect(disclosureSummary(await disclosureOf()).className).toContain("min-h-11");
  });

  it("opens and closes on click, revealing both tables in one expand", async () => {
    renderPanel("acme/widget", repo());
    const disclosure = await disclosureOf();
    activateDisclosure(disclosure, "click");
    await expectDisclosureState(disclosure, true);
    expect(within(disclosure).getByTestId("unblocker-aggregates-table")).toBeVisible();
    expect(within(disclosure).getByTestId("unblocker-workspaces-table")).toBeVisible();
    activateDisclosure(disclosure, "click");
    await expectDisclosureState(disclosure, false);
  });

  it("opens and closes by click, Enter and Space through the lifted open state and ignores other keys", async () => {
    renderPanel("acme/widget", repo());
    await expectTogglesOnEveryActivation(await disclosureOf());
  });

  it("keeps the disclosure open across a refetch of the same data", async () => {
    const view = (health: number) => (
      <ServerEventsContext.Provider
        value={{ counters: { workspace_list: 0, health, engines: 0 }, connected: true }}
      >
        <MemoryRouter>
          <UnblockerActivityPanel engine="acme/widget" />
        </MemoryRouter>
      </ServerEventsContext.Provider>
    );
    server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.json(repo())));
    const { rerender } = render(view(0));
    const disclosure = await disclosureOf();
    activateDisclosure(disclosure, "Enter");
    await expectDisclosureState(disclosure, true);
    rerender(view(1));
    await screen.findByTestId("unblocker-activity-summary");
    await expectDisclosureState(screen.getByTestId("unblocker-activity-disclosure"), true);
  });

  it("renders no disclosure when there is nothing to reveal, only the empty notice", async () => {
    renderPanel(
      "acme/widget",
      repo({ events_total: 0, workspaces_touched: 0, aggregates: [], events: [], workspaces: [] }),
    );
    await screen.findByTestId("unblocker-activity-empty-repo-window");
    expect(screen.queryByTestId("unblocker-activity-disclosure")).not.toBeInTheDocument();
  });

  it("reveals the park-kinds table and the unavailable-workspaces reason together on the fleet view", async () => {
    renderPanel(null, fleet());
    const disclosure = await disclosureOf();
    activateDisclosure(disclosure, "click");
    expect(within(disclosure).getByTestId("unblocker-aggregates-table")).toBeVisible();
    expect(within(disclosure).getByTestId("unblocker-workspaces-unavailable")).toBeVisible();
    expect(within(disclosure).queryByTestId("unblocker-workspaces-table")).not.toBeInTheDocument();
  });

  it("keeps the stale, error and loading notices outside the disclosure so they stay visible when collapsed", async () => {
    server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.error()));
    render(
      <MemoryRouter>
        <UnblockerActivityPanel engine={null} />
      </MemoryRouter>,
    );
    const alert = await screen.findByTestId("unblocker-activity-error");
    expect(alert.closest("details")).toBeNull();
    expect(screen.getByTestId("unblocker-activity-panel")).toContainElement(alert);
  });
});
