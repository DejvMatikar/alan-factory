import { describe, expect, it } from "vitest";

import type { UnblockActivityResponse, UnblockFleetActivity } from "../api";
import {
  humanize,
  outcomeTone,
  plural,
  unblockerCountEntries,
  unblockerCounts,
  unblockerDigestLine,
  unblockerHealthHref,
} from "./unblockerSummaryFields";

function fleet(overrides: Partial<UnblockFleetActivity> = {}): UnblockActivityResponse {
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
    aggregates: [],
    ever_recorded: true,
    unattributed_events: 0,
    ...overrides,
  };
}

describe("formatting helpers", () => {
  it("pluralises by count", () => {
    expect(plural(1, "event")).toBe("1 event");
    expect(plural(0, "event")).toBe("0 events");
    expect(plural(3, "workspace")).toBe("3 workspaces");
  });

  it("turns an underscore token into words", () => {
    expect(humanize("needs_human")).toBe("needs human");
  });

  it("colours failed and needs_human outcomes red and everything else neutral", () => {
    expect(outcomeTone("failed")).toBe("red");
    expect(outcomeTone("needs_human")).toBe("red");
    expect(outcomeTone("acted")).toBe("unknown");
    expect(outcomeTone("declined")).toBe("unknown");
  });
});

describe("unblockerCounts", () => {
  it("reads the four headline counts from the payload", () => {
    expect(unblockerCounts(fleet())).toEqual({ events: 6, touched: 4, stuck: 2, gaveUp: 1 });
  });

  it("reads an explicit zero still-stuck count as zero", () => {
    const counts = unblockerCounts(fleet({ still_stuck_workspaces: 0 }));
    expect(counts).toEqual({ events: 6, touched: 4, stuck: 0, gaveUp: 1 });
  });

  it("reads an explicit zero gave-up count as zero", () => {
    const counts = unblockerCounts(fleet({ gave_up_workspaces: 0 }));
    expect(counts).toEqual({ events: 6, touched: 4, stuck: 2, gaveUp: 0 });
  });
});

describe("unblockerDigestLine", () => {
  it("states events and workspaces touched, and adds the alarming counts only when non-zero", () => {
    expect(unblockerDigestLine({ events: 6, touched: 4, stuck: 0, gaveUp: 0 })).toBe(
      "6 events · 4 workspaces touched",
    );
    expect(unblockerDigestLine({ events: 1, touched: 1, stuck: 1, gaveUp: 0 })).toBe(
      "1 event · 1 workspace touched · 1 still stuck",
    );
    expect(unblockerDigestLine({ events: 6, touched: 4, stuck: 0, gaveUp: 2 })).toBe(
      "6 events · 4 workspaces touched · 2 gave up",
    );
    expect(unblockerDigestLine({ events: 6, touched: 4, stuck: 2, gaveUp: 1 })).toBe(
      "6 events · 4 workspaces touched · 2 still stuck · 1 gave up",
    );
  });
});

describe("unblockerCountEntries", () => {
  it("returns nothing when the window holds no events and no touched workspace", () => {
    expect(unblockerCountEntries({ events: 0, touched: 0, stuck: 0, gaveUp: 0 })).toEqual([]);
  });

  it("returns the two quiet neutral chips only when nothing is stuck or given up", () => {
    const entries = unblockerCountEntries({ events: 6, touched: 4, stuck: 0, gaveUp: 0 });
    expect(entries.map((e) => [e.key, e.value, e.tone])).toEqual([
      ["events", 6, "unknown"],
      ["touched", 4, "unknown"],
    ]);
  });

  it("appends a red still-stuck chip when a workspace is stuck", () => {
    const entries = unblockerCountEntries({ events: 6, touched: 4, stuck: 2, gaveUp: 0 });
    expect(entries.map((e) => e.key)).toEqual(["events", "touched", "stuck"]);
    expect(entries[2]).toMatchObject({ label: "Still stuck", value: 2, tone: "red" });
  });

  it("appends a red gave-up chip when the host gave up on a workspace", () => {
    const entries = unblockerCountEntries({ events: 6, touched: 4, stuck: 0, gaveUp: 3 });
    expect(entries.map((e) => e.key)).toEqual(["events", "touched", "gave-up"]);
    expect(entries[2]).toMatchObject({ label: "Gave up", value: 3, tone: "red" });
  });

  it("shows both alarming chips together, stuck before gave up", () => {
    const entries = unblockerCountEntries({ events: 6, touched: 4, stuck: 2, gaveUp: 1 });
    expect(entries.map((e) => e.key)).toEqual(["events", "touched", "stuck", "gave-up"]);
  });

  it("still shows the row when touched workspaces exist without events in the window", () => {
    expect(unblockerCountEntries({ events: 0, touched: 1, stuck: 0, gaveUp: 0 })).toHaveLength(2);
  });

  it("still shows the row when events exist without a touched workspace", () => {
    expect(unblockerCountEntries({ events: 1, touched: 0, stuck: 0, gaveUp: 0 })).toHaveLength(2);
  });
});

describe("unblockerHealthHref", () => {
  it("scopes the health page to the repository with an encoded slug", () => {
    expect(unblockerHealthHref("acme/widget")).toBe("/health?engine=acme%2Fwidget");
  });
});
