import { describe, expect, it } from "vitest";

import { CAVEATS, FUNNEL_STAGES, METRIC_GROUPS, OUTCOME_FLOOR } from "./repoMetricRows";

describe("METRIC_GROUPS", () => {
  it("orders the groups Delivery, Quality, Unblocker", () => {
    expect(METRIC_GROUPS.map((group) => group.title)).toEqual(["Delivery", "Quality", "Unblocker"]);
  });

  it("lists every windowed metric exactly once", () => {
    const keys = METRIC_GROUPS.flatMap((group) => group.rows.map((row) => row.key));
    expect([...keys].sort()).toEqual(
      [
        "autonomous",
        "build_success",
        "correction_recovery",
        "first_pass_approval",
        "format_error",
        "outcome",
        "rescue_load",
        "rescue_success",
        "unproven_clauses",
      ].sort(),
    );
  });

  it("never lists mean fix cycles to merge", () => {
    const labels = METRIC_GROUPS.flatMap((group) => group.rows.map((row) => row.label));
    expect(labels.join(" ").toLowerCase()).not.toContain("fix cycles");
  });

  it("gives every metric a one-sentence summary", () => {
    for (const row of METRIC_GROUPS.flatMap((group) => group.rows)) {
      expect(row.summary).not.toContain("\n");
      expect(row.summary.endsWith(".")).toBe(true);
    }
  });

  it("says lower is better in the summary of the two lower-is-better metrics", () => {
    const lower = METRIC_GROUPS.flatMap((group) => group.rows).filter((row) =>
      row.summary.includes("Lower is better"),
    );
    expect(lower.map((row) => row.key)).toEqual(["unproven_clauses", "format_error"]);
  });
});

describe("FUNNEL_STAGES", () => {
  it("lists the five funnel stages in order", () => {
    expect(FUNNEL_STAGES.map((stage) => stage.key)).toEqual([
      "not_crashed",
      "not_needed_human",
      "converged",
      "merged_pr",
      "dod_evidenced",
    ]);
  });
});

describe("CAVEATS", () => {
  it("has unique ids", () => {
    const ids = CAVEATS.map((caveat) => caveat.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("states that per-repository format and correction rates count only attributable records", () => {
    const formats = CAVEATS.find((caveat) => caveat.id === "formats");
    expect(formats?.text).toContain("only records that can be attributed to a repository");
  });
});

describe("OUTCOME_FLOOR", () => {
  it("is fifty percent", () => {
    expect(OUTCOME_FLOOR).toBe(0.5);
  });
});
