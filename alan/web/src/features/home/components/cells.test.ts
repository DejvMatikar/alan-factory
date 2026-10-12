import { describe, expect, it } from "vitest";

import {
  activeSecondsForFeature,
  doneCellForFeature,
  doneCellForProject,
  EMPTY_CELL,
  featurePrimaryLabel,
  formatCostCell,
  formatUsd,
  noteFirstLine,
  phaseCellForProject,
} from "./cells";

describe("formatUsd / formatCostCell", () => {
  it("formats a USD amount to two decimals with a leading dollar sign", () => {
    expect(formatUsd(1.5)).toBe("$1.50");
  });

  it("renders an em dash for a null cost", () => {
    expect(formatCostCell(null)).toBe(EMPTY_CELL);
  });

  it("renders an em dash for an undefined cost", () => {
    expect(formatCostCell(undefined)).toBe(EMPTY_CELL);
  });

  it("formats a defined cost at four decimal places", () => {
    expect(formatCostCell(2)).toBe("$2.0000");
  });

  it("surfaces a fraction-of-a-cent spend instead of rounding it away to zero", () => {
    expect(formatCostCell(0.0034)).toBe("$0.0034");
  });

  it("still renders the zero form for a genuinely zero cost", () => {
    expect(formatCostCell(0)).toBe("$0.0000");
  });

  it("diverges from formatUsd on sub-cent amounts: aggregates stay 2dp, per-workspace cost goes 4dp", () => {
    expect(formatUsd(0.0034)).toBe("$0.00");
    expect(formatCostCell(0.0034)).not.toBe(formatUsd(0.0034));
  });
});

describe("phaseCellForProject / doneCellForProject", () => {
  it("shows the honest planning degradation when no roadmap items exist", () => {
    const project = { roadmap_items_total: 0, roadmap_items_delivered: 0 };
    expect(phaseCellForProject(project)).toBe("planning · no plan yet");
    expect(doneCellForProject(project)).toBe(EMPTY_CELL);
  });

  it("shows the roadmap phase text and a K/M done fraction when a roadmap is imported", () => {
    const project = { roadmap_items_total: 6, roadmap_items_delivered: 2 };
    expect(phaseCellForProject(project)).toBe("roadmap · 2 of 6 items");
    expect(doneCellForProject(project)).toBe("2/6");
  });

  it("shows a zero done fraction when a roadmap exists and nothing has delivered", () => {
    const project = { roadmap_items_total: 3, roadmap_items_delivered: 0 };
    expect(phaseCellForProject(project)).toBe("roadmap · 0 of 3 items");
    expect(doneCellForProject(project)).toBe("0/3");
  });
});

describe("doneCellForFeature", () => {
  it("shows the not-done fraction for a one-task feature", () => {
    expect(doneCellForFeature({ tasks_total: 1, tasks_done: 0 })).toBe("0/1");
  });

  it("shows the done fraction for a one-task feature", () => {
    expect(doneCellForFeature({ tasks_total: 1, tasks_done: 1 })).toBe("1/1");
  });

  it("formats a tasks_done/tasks_total fraction for a multi-task feature", () => {
    expect(doneCellForFeature({ tasks_total: 3, tasks_done: 1 })).toBe("1/3");
  });

  it("formats a fully-done multi-task feature", () => {
    expect(doneCellForFeature({ tasks_total: 2, tasks_done: 2 })).toBe("2/2");
  });
});

describe("featurePrimaryLabel", () => {
  it("joins task id and title with a middle dot", () => {
    expect(featurePrimaryLabel({ task_id: "ws-1", title: "My Feature" })).toBe("ws-1 · My Feature");
  });
});

describe("noteFirstLine", () => {
  it("returns null for an undefined note", () => {
    expect(noteFirstLine({})).toBeNull();
  });

  it("returns null for a null note", () => {
    expect(noteFirstLine({ note: null })).toBeNull();
  });

  it("returns null for an empty note", () => {
    expect(noteFirstLine({ note: "" })).toBeNull();
  });

  it("returns null for a whitespace-only note", () => {
    expect(noteFirstLine({ note: "   " })).toBeNull();
  });

  it("trims surrounding whitespace from a single-line note", () => {
    expect(noteFirstLine({ note: "  hi  " })).toBe("hi");
  });

  it("returns only the first line of a multi-line note", () => {
    const line = noteFirstLine({ note: "line one\nline two" });
    expect(line).toBe("line one");
    expect(line).not.toContain("line two");
  });

  it("strips a trailing carriage return from a CRLF-terminated first line", () => {
    expect(noteFirstLine({ note: "a\r\nb" })).toBe("a");
  });

  it("returns null when the note starts with a newline and only later lines have content", () => {
    expect(noteFirstLine({ note: "\nlater" })).toBeNull();
  });

  it("returns a very long single-line note unchanged, uncapped", () => {
    const long = "x".repeat(5000);
    expect(noteFirstLine({ note: long })).toBe(long);
  });
});

describe("activeSecondsForFeature", () => {
  it("passes through a defined active_seconds value", () => {
    expect(activeSecondsForFeature({ active_seconds: 120 })).toBe(120);
  });

  it("treats a null active_seconds as null", () => {
    expect(activeSecondsForFeature({ active_seconds: null })).toBeNull();
  });

  it("treats a missing (undefined) active_seconds as null, not a crash", () => {
    expect(activeSecondsForFeature({})).toBeNull();
  });
});
