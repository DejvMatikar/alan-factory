import { describe, expect, it } from "vitest";

import type { RepoHealthRow, RepoHealthValue } from "../../../api";
import { healthWindow, metricWindows, repoRow, scopeWindows } from "../../../test/healthWindows";
import { orderedHealthRepoRows, SEVERITY_WEIGHT } from "./healthRepoRows";

function value(v: number | null, sample = 0, lowData = false): RepoHealthValue {
  return { value: v, sample, low_data: lowData };
}

function outcome(rate: number | null): RepoHealthRow["windows"] {
  const sample = rate === null ? 0 : 40;
  return scopeWindows({
    outcome: metricWindows(healthWindow(rate, sample), healthWindow(rate, sample)),
  });
}

describe("SEVERITY_WEIGHT", () => {
  it("weighs critical highest, then high, then medium", () => {
    expect(SEVERITY_WEIGHT).toEqual({ critical: 3, high: 2, medium: 1 });
  });
});

describe("orderedHealthRepoRows", () => {
  it("orders by weighted outstanding findings descending", () => {
    const rows = [
      repoRow("a/quiet", { outstanding_findings: value(1) }),
      repoRow("b/loud", { outstanding_findings: value(5) }),
      repoRow("c/medium", { outstanding_findings: value(3) }),
    ];
    expect(orderedHealthRepoRows(rows).map((r) => r.repo)).toEqual([
      "b/loud",
      "c/medium",
      "a/quiet",
    ]);
  });

  it("breaks a findings tie by 30-day outcome ascending, worst outcome first", () => {
    const rows = [
      repoRow("a/good", { outstanding_findings: value(2), windows: outcome(0.9) }),
      repoRow("b/bad", { outstanding_findings: value(2), windows: outcome(0.2) }),
    ];
    expect(orderedHealthRepoRows(rows).map((r) => r.repo)).toEqual(["b/bad", "a/good"]);
  });

  it("sorts a null 30-day outcome last within a findings tie", () => {
    const rows = [
      repoRow("a/unknown", { outstanding_findings: value(2), windows: outcome(null) }),
      repoRow("b/known", { outstanding_findings: value(2), windows: outcome(0.5) }),
    ];
    expect(orderedHealthRepoRows(rows).map((r) => r.repo)).toEqual(["b/known", "a/unknown"]);
  });

  it("sorts a null 30-day outcome last regardless of input order", () => {
    const rows = [
      repoRow("b/known", { outstanding_findings: value(2), windows: outcome(0.5) }),
      repoRow("a/unknown", { outstanding_findings: value(2), windows: outcome(null) }),
    ];
    expect(orderedHealthRepoRows(rows).map((r) => r.repo)).toEqual(["b/known", "a/unknown"]);
  });

  it("treats a null outstanding-findings value as zero", () => {
    const rows = [
      repoRow("a/positive", { outstanding_findings: value(1) }),
      repoRow("b/null", { outstanding_findings: value(null) }),
    ];
    expect(orderedHealthRepoRows(rows).map((r) => r.repo)).toEqual(["a/positive", "b/null"]);
  });

  it("treats a null outstanding-findings value as zero regardless of input order", () => {
    const rows = [
      repoRow("b/null", { outstanding_findings: value(null) }),
      repoRow("a/positive", { outstanding_findings: value(1) }),
    ];
    expect(orderedHealthRepoRows(rows).map((r) => r.repo)).toEqual(["a/positive", "b/null"]);
  });

  it("treats two null 30-day outcomes as tied", () => {
    const rows = [
      repoRow("z/last", { outstanding_findings: value(2), windows: outcome(null) }),
      repoRow("a/first", { outstanding_findings: value(2), windows: outcome(null) }),
    ];
    expect(orderedHealthRepoRows(rows).map((r) => r.repo)).toEqual(["a/first", "z/last"]);
  });

  it("breaks a full tie by repository slug ascending", () => {
    const rows = [
      repoRow("z/repo", { outstanding_findings: value(1), windows: outcome(0.5) }),
      repoRow("a/repo", { outstanding_findings: value(1), windows: outcome(0.5) }),
    ];
    expect(orderedHealthRepoRows(rows).map((r) => r.repo)).toEqual(["a/repo", "z/repo"]);
  });

  it("does not mutate the input array", () => {
    const rows = [repoRow("b/repo"), repoRow("a/repo")];
    const original = [...rows];
    orderedHealthRepoRows(rows);
    expect(rows).toEqual(original);
  });

  it("returns an empty array for no rows", () => {
    expect(orderedHealthRepoRows([])).toEqual([]);
  });
});
