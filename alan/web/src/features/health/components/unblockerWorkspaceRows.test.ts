import { describe, expect, it } from "vitest";

import type {
  UnblockActivityEvent,
  UnblockScopedActivity,
  UnblockWorkspaceActivity,
} from "../../../api";
import {
  attemptsLabel,
  buildWorkspaceRows,
  clearanceChipOf,
  eventsCutByCap,
  GAVE_UP_TITLE,
  type UnblockerWorkspaceRow,
} from "./unblockerWorkspaceRows";

function source(overrides: Partial<UnblockWorkspaceActivity> = {}): UnblockWorkspaceActivity {
  return {
    workspace_key: "ws-a",
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
    workspace_key: "ws-a",
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
    ...overrides,
  };
}

function activity(
  workspaces: UnblockWorkspaceActivity[],
  events: UnblockActivityEvent[],
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
  };
}

function keys(rows: readonly UnblockerWorkspaceRow[]): string[] {
  return rows.map((row) => row.workspaceKey);
}

describe("buildWorkspaceRows", () => {
  it("produces one row per workspace carrying the aggregated fields from the payload", () => {
    const diagnosis = { signature: "sig", proposed_disposition: null, verified: true };
    const rows = buildWorkspaceRows(
      activity(
        [
          source({
            events_total: 4,
            attempts_total: 3,
            clearance: "still_stuck",
            gave_up: true,
            issue_url: "https://github.com/acme/widget/issues/3",
            diagnosis,
          }),
        ],
        [event()],
      ),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      workspaceKey: "ws-a",
      lastAt: "2026-09-19T14:05Z",
      shape: "merge_park",
      eventsTotal: 4,
      attemptsTotal: 3,
      attemptsCeiling: 5,
      clearance: "still_stuck",
      gaveUp: true,
      issueUrl: "https://github.com/acme/widget/issues/3",
      diagnosis,
    });
  });

  it("attaches each workspace's own listed events in the order the payload gave them", () => {
    const rows = buildWorkspaceRows(
      activity(
        [source({ workspace_key: "ws-a", events_total: 2 }), source({ workspace_key: "ws-b" })],
        [
          event({ id: 4, workspace_key: "ws-b" }),
          event({ id: 3, workspace_key: "ws-a" }),
          event({ id: 2, workspace_key: "ws-a" }),
        ],
      ),
    );
    const byKey = new Map(rows.map((row) => [row.workspaceKey, row]));
    expect(byKey.get("ws-a")?.events.map((e) => e.id)).toEqual([3, 2]);
    expect(byKey.get("ws-b")?.events.map((e) => e.id)).toEqual([4]);
  });

  it("keeps a workspace whose events were all cut by the event cap, with no child events", () => {
    const rows = buildWorkspaceRows(
      activity([source({ workspace_key: "ws-old", events_total: 3, events_listed: 0 })], []),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]?.events).toEqual([]);
    expect(rows[0] !== undefined && eventsCutByCap(rows[0])).toBe(true);
  });

  it("returns no rows when the payload lists no workspaces", () => {
    expect(buildWorkspaceRows(activity([], [event()]))).toEqual([]);
  });

  it("sorts still-stuck and gave-up workspaces above the rest", () => {
    const rows = buildWorkspaceRows(
      activity(
        [
          source({ workspace_key: "calm-newest", last_at: "2026-09-19T12:00Z" }),
          source({ workspace_key: "stuck-old", last_at: "2026-09-01T12:00Z", clearance: "still_stuck" }),
          source({ workspace_key: "gave-up-older", last_at: "2026-08-20T12:00Z", gave_up: true }),
          source({ workspace_key: "calm-old", last_at: "2026-09-02T12:00Z" }),
        ],
        [],
      ),
    );
    expect(keys(rows)).toEqual(["stuck-old", "gave-up-older", "calm-newest", "calm-old"]);
  });

  it("orders most recent activity first within each band", () => {
    const rows = buildWorkspaceRows(
      activity(
        [
          source({ workspace_key: "calm-1", last_at: "2026-09-10T08:00Z" }),
          source({ workspace_key: "stuck-1", last_at: "2026-09-05T08:00Z", clearance: "still_stuck" }),
          source({ workspace_key: "calm-2", last_at: "2026-09-12T08:00Z" }),
          source({ workspace_key: "stuck-2", last_at: "2026-09-06T08:00Z", gave_up: true }),
        ],
        [],
      ),
    );
    expect(keys(rows)).toEqual(["stuck-2", "stuck-1", "calm-2", "calm-1"]);
  });

  it("orders instants by time rather than by string when the timestamps differ in precision", () => {
    const rows = buildWorkspaceRows(
      activity(
        [
          source({ workspace_key: "earlier", last_at: "2026-09-19T14:05:33Z" }),
          source({ workspace_key: "later", last_at: "2026-09-19T14:06Z" }),
        ],
        [],
      ),
    );
    expect(keys(rows)).toEqual(["later", "earlier"]);
  });

  it("treats a workspace that is gone or cleared and never gave up as calm", () => {
    const rows = buildWorkspaceRows(
      activity(
        [
          source({ workspace_key: "gone", clearance: "unknown", last_at: "2026-09-01T00:00Z" }),
          source({ workspace_key: "cleared", clearance: "cleared", last_at: "2026-09-02T00:00Z" }),
        ],
        [],
      ),
    );
    expect(rows.map((row) => row.needsAttention)).toEqual([false, false]);
  });
});

describe("row derivations", () => {
  function row(overrides: Partial<UnblockerWorkspaceRow> = {}): UnblockerWorkspaceRow {
    return {
      workspaceKey: "ws-a",
      lastAt: "2026-09-19T14:05Z",
      shape: "merge_park",
      eventsTotal: 1,
      attemptsTotal: 2,
      attemptsCeiling: 5,
      clearance: "cleared",
      gaveUp: false,
      issueUrl: null,
      diagnosis: null,
      events: [],
      needsAttention: false,
      ...overrides,
    };
  }

  it("words attempts as N of the ceiling", () => {
    expect(attemptsLabel(row({ attemptsTotal: 3, attemptsCeiling: 5 }))).toBe("Attempt 3 of 5");
  });

  it("uses an em dash when the host made no attempt", () => {
    expect(attemptsLabel(row({ attemptsTotal: 0 }))).toBe("—");
  });

  it("flags a workspace as cut only when fewer events are listed than it recorded", () => {
    expect(eventsCutByCap(row({ eventsTotal: 2, events: [event()] }))).toBe(true);
    expect(eventsCutByCap(row({ eventsTotal: 1, events: [event()] }))).toBe(false);
  });

  it.each([
    ["cleared", "green", "cleared"],
    ["still_stuck", "red", "still stuck"],
    ["unknown", "unknown", "workspace gone"],
  ] as const)("maps clearance %s to a %s chip labelled %s", (clearance, tone, label) => {
    expect(clearanceChipOf(clearance)).toEqual({ tone, label });
  });

  it("names all three causes of a give-up in its title", () => {
    expect(GAVE_UP_TITLE).toContain("attempt ceiling");
    expect(GAVE_UP_TITLE).toContain("human-only authority holder");
    expect(GAVE_UP_TITLE).toContain("terminal");
  });
});
