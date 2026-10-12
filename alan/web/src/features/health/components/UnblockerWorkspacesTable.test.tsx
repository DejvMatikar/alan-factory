import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type {
  UnblockActivityEvent,
  UnblockScopedActivity,
  UnblockWorkspaceActivity,
} from "../../../api";
import UnblockerWorkspacesTable from "./UnblockerWorkspacesTable";

function workspace(overrides: Partial<UnblockWorkspaceActivity> = {}): UnblockWorkspaceActivity {
  return {
    workspace_key: "ws-alpha",
    last_at: "2026-09-19T14:05Z",
    shape: "merge_park",
    events_total: 1,
    events_listed: 1,
    attempts_total: 2,
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

function scoped(
  workspaces: UnblockWorkspaceActivity[],
  events: UnblockActivityEvent[],
  overrides: Partial<UnblockScopedActivity> = {},
): UnblockScopedActivity {
  return {
    scope: "repo",
    repo: "acme/widget",
    window_days: 30,
    events_total: events.length,
    workspaces_touched: workspaces.length,
    waited_workspaces: 0,
    diagnosed_workspaces: 0,
    diagnosable_workspaces: 0,
    still_stuck_workspaces: 0,
    gave_up_workspaces: 0,
    aggregates: [],
    fleet_ever_recorded: true,
    events,
    workspaces,
    ...overrides,
  };
}

function renderTable(activity: UnblockScopedActivity) {
  return render(
    <MemoryRouter>
      <UnblockerWorkspacesTable activity={activity} />
    </MemoryRouter>,
  );
}

function rowOf(key: string): HTMLElement {
  return screen.getByTestId(`unblocker-workspace-${key}`);
}

function toggleOf(key: string): HTMLElement {
  return screen.getByTestId(`unblocker-workspace-toggle-${key}`);
}

describe("UnblockerWorkspacesTable — rows", () => {
  it("renders nothing when the repository has no touched workspaces", () => {
    const { container } = renderTable(scoped([], []));
    expect(container).toBeEmptyDOMElement();
  });

  it("names the repository and how many workspaces it lists", () => {
    renderTable(scoped([workspace(), workspace({ workspace_key: "ws-beta" })], [event()]));
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading).toHaveTextContent("Workspaces — acme/widget");
    expect(heading).toHaveTextContent("2 touched");
  });

  it("renders one aggregated row per touched workspace, however many events it has", () => {
    renderTable(
      scoped(
        [workspace({ events_total: 3 })],
        [event({ id: 3 }), event({ id: 2 }), event({ id: 1 })],
      ),
    );
    expect(screen.getAllByRole("rowheader")).toHaveLength(1);
    expect(rowOf("ws-alpha")).toBeInTheDocument();
  });

  it("labels every column", () => {
    renderTable(scoped([workspace()], [event()]));
    const headers = screen.getAllByRole("columnheader").map((header) => header.textContent);
    expect(headers).toEqual([
      "Expand events",
      "Workspace",
      "Last acted on",
      "Park kind",
      "Events",
      "Attempts",
      "Now",
      "Gave up",
      "Issue",
      "Latest diagnosis",
    ]);
  });

  it("links the workspace to its own page with the standard tap height", () => {
    renderTable(scoped([workspace()], [event()]));
    const link = within(rowOf("ws-alpha")).getByRole("link", { name: "ws-alpha" });
    expect(link).toHaveAttribute("href", "/workspaces/ws-alpha");
    expect(link.className).toContain("min-h-11");
  });

  it("shows when the workspace was last acted on, the park kind and the event count", () => {
    renderTable(scoped([workspace({ events_total: 7, shape: "question_park" })], [event()]));
    const row = rowOf("ws-alpha");
    expect(within(row).getByText("2026-09-19 14:05 UTC")).toHaveAttribute("datetime", "2026-09-19T14:05Z");
    expect(within(row).getByText("question_park")).toBeInTheDocument();
    expect(within(row).getByText("7")).toBeInTheDocument();
  });

  it("words attempts as Attempt N of the ceiling", () => {
    renderTable(scoped([workspace({ attempts_total: 3, attempts_ceiling: 5 })], [event()]));
    expect(within(rowOf("ws-alpha")).getByText("Attempt 3 of 5")).toBeInTheDocument();
  });

  it("shows an em dash instead of Attempt 0 of 5 when the host made no attempt", () => {
    renderTable(scoped([workspace({ attempts_total: 0 })], [event({ kind: "report" })]));
    const row = rowOf("ws-alpha");
    expect(within(row).queryByText(/Attempt/)).not.toBeInTheDocument();
    expect(within(row).getAllByRole("cell")[4]).toHaveTextContent("—");
  });

  it.each([
    ["cleared", "cleared", "status-success"],
    ["still_stuck", "still stuck", "status-danger"],
    ["unknown", "workspace gone", "status-neutral"],
  ] as const)("distinguishes clearance %s by the text %s and not by colour alone", (clearance, text, tone) => {
    renderTable(scoped([workspace({ clearance })], [event()]));
    const chip = screen.getByTestId("aggregate-chip-unblocker-workspace-clearance-ws-alpha");
    expect(chip).toHaveTextContent(text);
    expect(chip.className).toContain(tone);
  });

  it("marks a workspace the host gave up on with a red chip whose title names the three causes", () => {
    renderTable(scoped([workspace({ gave_up: true })], [event({ kind: "report" })]));
    const chip = screen.getByTestId("aggregate-chip-unblocker-workspace-gave-up-ws-alpha");
    expect(chip).toHaveTextContent("Gave up");
    expect(chip.className).toContain("status-danger");
    const title = chip.parentElement?.getAttribute("title") ?? "";
    expect(title).toContain("attempt ceiling");
    expect(title).toContain("human-only authority holder");
    expect(title).toContain("terminal");
  });

  it("says No, with no chip, for a workspace the host did not give up on", () => {
    renderTable(scoped([workspace({ gave_up: false })], [event()]));
    expect(within(rowOf("ws-alpha")).getByText("No")).toBeInTheDocument();
    expect(screen.queryByTestId("aggregate-chip-unblocker-workspace-gave-up-ws-alpha")).not.toBeInTheDocument();
  });

  it("links the filed issue in a new tab with a visible cue and the stored url", () => {
    renderTable(scoped([workspace({ issue_url: "https://github.com/acme/widget/issues/12" })], [event()]));
    const link = screen.getByTestId("unblocker-issue-link-ws-alpha");
    expect(link).toHaveAttribute("href", "https://github.com/acme/widget/issues/12");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("Filed issue");
    expect(link).toHaveTextContent("(opens in a new tab)");
    expect(link.className).toContain("min-h-11");
  });

  it("renders no issue link when none was filed", () => {
    renderTable(scoped([workspace({ issue_url: null })], [event()]));
    expect(screen.queryByTestId("unblocker-issue-link-ws-alpha")).not.toBeInTheDocument();
  });

  it("renders the latest diagnosis with signature, proposed disposition and verification", () => {
    renderTable(
      scoped(
        [workspace({ diagnosis: { signature: "sig-flaky-ci", proposed_disposition: "known_flake", verified: true } })],
        [event()],
      ),
    );
    const line = screen.getByTestId("unblocker-diagnosis-ws-alpha");
    expect(line).toHaveTextContent("sig-flaky-ci");
    expect(line).toHaveTextContent("proposed disposition known flake");
    expect(line).toHaveTextContent("verified");
    expect(line).not.toHaveTextContent("unverified");
  });

  it("marks a diagnosis as unverified and omits the disposition when there is none", () => {
    renderTable(
      scoped(
        [workspace({ diagnosis: { signature: "sig-x", proposed_disposition: null, verified: false } })],
        [event()],
      ),
    );
    const line = screen.getByTestId("unblocker-diagnosis-ws-alpha");
    expect(line).toHaveTextContent("unverified");
    expect(line).not.toHaveTextContent("proposed disposition");
  });

  it("renders no diagnosis text when the workspace has none", () => {
    renderTable(scoped([workspace({ diagnosis: null })], [event()]));
    expect(screen.queryByTestId("unblocker-diagnosis-ws-alpha")).not.toBeInTheDocument();
  });

  it("sorts still-stuck and gave-up workspaces above the rest, most recent first within each band", () => {
    renderTable(
      scoped(
        [
          workspace({ workspace_key: "calm-new", last_at: "2026-09-19T10:00Z" }),
          workspace({ workspace_key: "stuck-old", last_at: "2026-09-01T10:00Z", clearance: "still_stuck" }),
          workspace({ workspace_key: "calm-old", last_at: "2026-09-02T10:00Z" }),
          workspace({ workspace_key: "gave-up-new", last_at: "2026-09-10T10:00Z", gave_up: true }),
        ],
        [],
      ),
    );
    expect(screen.getAllByRole("rowheader").map((header) => header.textContent)).toEqual([
      "gave-up-new",
      "stuck-old",
      "calm-new",
      "calm-old",
    ]);
  });

  it("scrolls horizontally inside a keyboard-focusable labelled region instead of overflowing", () => {
    renderTable(scoped([workspace()], [event()]));
    const region = screen.getByLabelText("Workspaces table, scroll horizontally for more columns");
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.className).toContain("overflow-x-auto");
    expect(region.className).toContain("relative");
  });
});

describe("UnblockerWorkspacesTable — expandable events", () => {
  function twoEvents() {
    return renderTable(
      scoped(
        [workspace({ events_total: 2 })],
        [event({ id: 2, at: "2026-09-19T14:05Z", outcome: "failed", clearance: "still_stuck" }), event({ id: 1, at: "2026-09-18T09:00Z" })],
      ),
    );
  }

  it("keeps the child events in the DOM but hidden until the row is expanded", () => {
    twoEvents();
    expect(screen.getByTestId("unblocker-event-2")).toHaveAttribute("hidden");
    expect(screen.getByTestId("unblocker-event-1")).toHaveAttribute("hidden");
    expect(screen.getByTestId("unblocker-event-2")).not.toBeVisible();
  });

  it("announces the collapsed state and controls exactly its child rows", () => {
    twoEvents();
    const toggle = toggleOf("ws-alpha");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAccessibleName("Events for ws-alpha");
    const controlled = (toggle.getAttribute("aria-controls") ?? "").split(" ");
    expect(controlled).toHaveLength(2);
    expect(controlled).toEqual([
      screen.getByTestId("unblocker-event-2").id,
      screen.getByTestId("unblocker-event-1").id,
    ]);
  });

  it("reveals the child rows and announces the expanded state on click, and hides them again", () => {
    twoEvents();
    const toggle = toggleOf("ws-alpha");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("unblocker-event-2")).toBeVisible();
    expect(screen.getByTestId("unblocker-event-1")).toBeVisible();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("unblocker-event-2")).not.toBeVisible();
  });

  it("expands one workspace without expanding another", () => {
    renderTable(
      scoped(
        [workspace({ workspace_key: "ws-a" }), workspace({ workspace_key: "ws-b" })],
        [event({ id: 2, workspace_key: "ws-b" }), event({ id: 1, workspace_key: "ws-a" })],
      ),
    );
    fireEvent.click(toggleOf("ws-a"));
    expect(screen.getByTestId("unblocker-event-1")).toBeVisible();
    expect(screen.getByTestId("unblocker-event-2")).not.toBeVisible();
    expect(toggleOf("ws-b")).toHaveAttribute("aria-expanded", "false");
  });

  it("gives the toggle the standard minimum hit area and makes it a real button", () => {
    twoEvents();
    const toggle = toggleOf("ws-alpha");
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle).toHaveAttribute("type", "button");
    expect(toggle.className).toContain("min-h-11");
    expect(toggle.className).toContain("min-w-11");
  });

  it("never uses a details element inside the table body", () => {
    twoEvents();
    expect(document.querySelector("tbody details")).toBeNull();
    expect(document.querySelector("details")).toBeNull();
  });

  it("puts each child event on one row spanning the table, with no field grid", () => {
    twoEvents();
    const child = screen.getByTestId("unblocker-event-2");
    expect(child.querySelectorAll("td")).toHaveLength(1);
    expect(child.querySelector("td")).toHaveAttribute("colspan", "10");
    expect(child.querySelector("dl")).toBeNull();
  });

  it("shows a child attempt's time, outcome, clearance, action and ledger attempt wording", () => {
    twoEvents();
    const child = screen.getByTestId("unblocker-event-1");
    expect(within(child).getByText("2026-09-18 09:00 UTC")).toHaveAttribute("datetime", "2026-09-18T09:00Z");
    expect(within(child).getByTestId("aggregate-chip-unblocker-outcome-1")).toHaveTextContent("acted");
    expect(within(child).getByTestId("aggregate-chip-unblocker-clearance-1")).toHaveTextContent("cleared");
    expect(within(child).getByText("Host did rebase_branch")).toBeInTheDocument();
    expect(within(child).getByTestId("aggregate-chip-unblocker-repetition-1")).toHaveTextContent("attempt 1 of 2");
    expect(within(child).getByText("workspace attempt 1 of 3")).toBeInTheDocument();
  });

  it("keeps outcome and clearance as two separate chips", () => {
    twoEvents();
    const child = screen.getByTestId("unblocker-event-2");
    const outcome = within(child).getByTestId("aggregate-chip-unblocker-outcome-2");
    const clearance = within(child).getByTestId("aggregate-chip-unblocker-clearance-2");
    expect(outcome).toHaveTextContent("failed");
    expect(clearance).toHaveTextContent("still stuck");
    expect(outcome).not.toBe(clearance);
  });

  it("uses a neutral tone for a first attempt without the stop wording", () => {
    twoEvents();
    const chip = screen.getByTestId("aggregate-chip-unblocker-repetition-1");
    expect(chip).not.toHaveTextContent("the host stops after this");
    expect(chip.className).toContain("status-neutral");
  });

  it("escalates a repeated attempt below the ceiling to the transitional tone", () => {
    renderTable(
      scoped(
        [workspace()],
        [event({ attempt: { fingerprint_index: 2, fingerprint_ceiling: 3, workspace_index: 2, workspace_ceiling: 3 } })],
      ),
    );
    const chip = screen.getByTestId("aggregate-chip-unblocker-repetition-1");
    expect(chip).toHaveTextContent("attempt 2 of 3");
    expect(chip).not.toHaveTextContent("the host stops after this");
    expect(chip.className).toContain("status-warning");
  });

  it("marks an attempt at or over the ceiling red and says the host stops after it", () => {
    renderTable(
      scoped(
        [workspace()],
        [event({ attempt: { fingerprint_index: 3, fingerprint_ceiling: 2, workspace_index: 3, workspace_ceiling: 3 } })],
      ),
    );
    const chip = screen.getByTestId("aggregate-chip-unblocker-repetition-1");
    expect(chip).toHaveTextContent("attempt 3 of 2 — the host stops after this");
    expect(chip.className).toContain("status-danger");
  });

  it("renders no repetition chip and dashes the action when an attempt carries no index", () => {
    renderTable(scoped([workspace()], [event({ attempt: null, action: null })]));
    const child = screen.getByTestId("unblocker-event-1");
    expect(within(child).queryByTestId("aggregate-chip-unblocker-repetition-1")).not.toBeInTheDocument();
    expect(within(child).queryByText(/workspace attempt/)).not.toBeInTheDocument();
    expect(within(child).getByText("Host did —")).toBeInTheDocument();
  });

  it("renders a report as gave up after N attempts with its reason and authority holder", () => {
    renderTable(
      scoped(
        [workspace({ gave_up: true })],
        [
          event({
            kind: "report",
            action: null,
            outcome: "needs_human",
            reason: "ceiling_reached",
            authority_holder: "human_operator",
            attempt: null,
            attempts_total: 3,
            clearance: "still_stuck",
          }),
        ],
      ),
    );
    const child = screen.getByTestId("unblocker-event-1");
    expect(within(child).getByTestId("aggregate-chip-unblocker-gave-up-1")).toHaveTextContent("gave up after 3 attempts");
    expect(within(child).getByText("Reason ceiling_reached")).toBeInTheDocument();
    expect(within(child).getByText("Authority holder human_operator")).toBeInTheDocument();
    expect(within(child).queryByTestId("aggregate-chip-unblocker-repetition-1")).not.toBeInTheDocument();
    expect(within(child).queryByText(/Host did/)).not.toBeInTheDocument();
  });

  it("uses singular wording for a single attempt before giving up", () => {
    renderTable(scoped([workspace()], [event({ kind: "report", attempt: null, attempts_total: 1 })]));
    const chip = screen.getByTestId("aggregate-chip-unblocker-gave-up-1");
    expect(chip).toHaveTextContent("gave up after 1 attempt");
    expect(chip).not.toHaveTextContent("attempts");
  });

  it("renders a report without an attempt total or reason as plain gave up with dashes", () => {
    renderTable(
      scoped([workspace()], [event({ kind: "report", attempt: null, attempts_total: null, reason: null, authority_holder: null })]),
    );
    const child = screen.getByTestId("unblocker-event-1");
    expect(within(child).getByTestId("aggregate-chip-unblocker-gave-up-1")).not.toHaveTextContent("after");
    expect(within(child).getByText("Reason —")).toBeInTheDocument();
    expect(within(child).getByText("Authority holder —")).toBeInTheDocument();
  });

  it("renders no gave-up chip for an attempt event", () => {
    renderTable(scoped([workspace()], [event()]));
    expect(screen.queryByTestId("aggregate-chip-unblocker-gave-up-1")).not.toBeInTheDocument();
  });
});

describe("UnblockerWorkspacesTable — event cap honesty", () => {
  it("adds no per-row note and no panel note when every event is listed", () => {
    renderTable(scoped([workspace({ events_total: 1 })], [event()]));
    expect(screen.queryByTestId("unblocker-workspace-cap-ws-alpha")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblocker-events-cap")).not.toBeInTheDocument();
  });

  it("states showing k of n events on the row whose events were cut, hidden until it is expanded", () => {
    renderTable(scoped([workspace({ events_total: 7 })], [event({ id: 2 }), event({ id: 1 })], { events_total: 7 }));
    const note = screen.getByTestId("unblocker-workspace-cap-ws-alpha");
    expect(note).toHaveTextContent("Showing 2 of 7 events");
    expect(note).not.toBeVisible();
    fireEvent.click(toggleOf("ws-alpha"));
    expect(note).toBeVisible();
  });

  it("controls the cap note along with the child rows", () => {
    renderTable(scoped([workspace({ events_total: 7 })], [event({ id: 2 }), event({ id: 1 })], { events_total: 7 }));
    const controlled = (toggleOf("ws-alpha").getAttribute("aria-controls") ?? "").split(" ");
    expect(controlled).toHaveLength(3);
    expect(controlled[0]).toBe(screen.getByTestId("unblocker-workspace-cap-ws-alpha").id);
  });

  it("notes only the rows that were cut, not their untouched neighbours", () => {
    renderTable(
      scoped(
        [workspace({ workspace_key: "ws-a", events_total: 4 }), workspace({ workspace_key: "ws-b", events_total: 1 })],
        [event({ id: 2, workspace_key: "ws-b" }), event({ id: 1, workspace_key: "ws-a" })],
        { events_total: 5 },
      ),
    );
    expect(screen.getByTestId("unblocker-workspace-cap-ws-a")).toHaveTextContent("Showing 1 of 4 events");
    expect(screen.queryByTestId("unblocker-workspace-cap-ws-b")).not.toBeInTheDocument();
  });

  it("keeps a workspace whose every event was cut, listing the note and no child rows", () => {
    renderTable(scoped([workspace({ events_total: 3, events_listed: 0 })], [], { events_total: 3 }));
    expect(screen.getByTestId("unblocker-workspace-cap-ws-alpha")).toHaveTextContent("Showing 0 of 3 events");
    expect(document.querySelector('[data-testid^="unblocker-event-"]')).toBeNull();
    expect(toggleOf("ws-alpha")).toHaveAttribute("aria-controls", screen.getByTestId("unblocker-workspace-cap-ws-alpha").id);
  });

  it("states the cap once at panel level and says the counts cover the whole window", () => {
    renderTable(scoped([workspace({ events_total: 73 })], [event()], { events_total: 73 }));
    const cap = screen.getByTestId("unblocker-events-cap");
    expect(cap).toHaveTextContent("latest 1 of 73");
    expect(cap).toHaveTextContent("cover the whole window");
  });

  it("lists as many rows as the workspaces-touched figure states", () => {
    renderTable(
      scoped(
        [workspace({ workspace_key: "ws-a" }), workspace({ workspace_key: "ws-b" }), workspace({ workspace_key: "ws-c" })],
        [event({ id: 1, workspace_key: "ws-a" })],
        { workspaces_touched: 3, events_total: 9 },
      ),
    );
    expect(screen.getAllByRole("rowheader")).toHaveLength(3);
  });
});

describe("UnblockerWorkspacesTable — read-only", () => {
  it("offers only row toggles and links, never a form control", () => {
    renderTable(
      scoped(
        [workspace({ issue_url: "https://github.com/acme/widget/issues/12" })],
        [event()],
      ),
    );
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getAllByRole("link")).toHaveLength(2);
    expect(document.querySelector("input, select, textarea, form")).toBeNull();
  });
});
