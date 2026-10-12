import { describe, expect, it } from "vitest";

import {
  assertBaselineNotRaised,
  compareToBaseline,
  formatComparisonDiff,
  type RatchetBaseline,
  type ScreenCountsEntry,
} from "./baseline-ratchet";

type Dim = "alpha" | "beta";
const DIMENSIONS: readonly Dim[] = ["alpha", "beta"];
const EMPTY: Record<Dim, number> = { alpha: 0, beta: 0 };

function entry(name: string, alpha: number, beta: number): ScreenCountsEntry<Dim> {
  return { name, counts: { alpha, beta } };
}

describe("compareToBaseline", () => {
  it("passes when current counts stay at or below the baseline for every dimension", () => {
    const baseline: RatchetBaseline<Dim> = { home: { alpha: 2, beta: 1 } };
    const result = compareToBaseline(DIMENSIONS, [entry("home", 2, 0)], baseline, EMPTY);

    expect(result.pass).toBe(true);
    expect(result.screens).toEqual([
      {
        name: "home",
        pass: true,
        dimensions: [
          { dimension: "alpha", baselineCount: 2, currentCount: 2, pass: true },
          { dimension: "beta", baselineCount: 1, currentCount: 0, pass: true },
        ],
      },
    ]);
  });

  it("fails a dimension that regresses beyond a zero baseline", () => {
    const baseline: RatchetBaseline<Dim> = { home: { alpha: 0, beta: 0 } };
    const result = compareToBaseline(DIMENSIONS, [entry("home", 1, 0)], baseline, EMPTY);

    expect(result.pass).toBe(false);
    expect(result.screens[0]?.dimensions[0]).toEqual({
      dimension: "alpha",
      baselineCount: 0,
      currentCount: 1,
      pass: false,
    });
  });

  it("grandfathers a screen missing from the baseline as zero-tolerance and passes it clean", () => {
    const result = compareToBaseline(DIMENSIONS, [entry("settings", 0, 0)], {}, EMPTY);
    expect(result.pass).toBe(true);
  });

  it("fails a screen missing from the baseline once it carries any violation", () => {
    const result = compareToBaseline(DIMENSIONS, [entry("settings", 1, 0)], {}, EMPTY);
    expect(result.pass).toBe(false);
  });

  it("fails overall when any one of several screens regresses", () => {
    const baseline: RatchetBaseline<Dim> = {
      home: { alpha: 0, beta: 0 },
      settings: { alpha: 0, beta: 0 },
    };
    const result = compareToBaseline(
      DIMENSIONS,
      [entry("home", 0, 0), entry("settings", 1, 0)],
      baseline,
      EMPTY,
    );

    expect(result.pass).toBe(false);
    expect(result.screens[0]?.pass).toBe(true);
    expect(result.screens[1]?.pass).toBe(false);
  });
});

describe("assertBaselineNotRaised", () => {
  it("does not throw when counts are unchanged", () => {
    const baseline: RatchetBaseline<Dim> = { home: { alpha: 1, beta: 11 } };
    expect(() => assertBaselineNotRaised(DIMENSIONS, baseline, baseline, "test-UI", EMPTY)).not.toThrow();
  });

  it("does not throw when a baseline count is lowered", () => {
    const oldBaseline: RatchetBaseline<Dim> = { home: { alpha: 1, beta: 11 } };
    const newBaseline: RatchetBaseline<Dim> = { home: { alpha: 1, beta: 9 } };
    expect(() =>
      assertBaselineNotRaised(DIMENSIONS, oldBaseline, newBaseline, "test-UI", EMPTY),
    ).not.toThrow();
  });

  it("throws with the label prefix when a dimension count is raised for an existing screen", () => {
    const oldBaseline: RatchetBaseline<Dim> = { home: { alpha: 1, beta: 11 } };
    const newBaseline: RatchetBaseline<Dim> = { home: { alpha: 1, beta: 12 } };
    expect(() =>
      assertBaselineNotRaised(DIMENSIONS, oldBaseline, newBaseline, "test-UI", EMPTY),
    ).toThrow(/^test-UI baseline regression: screen "home" dimension "beta" raised from 11 to 12/);
  });

  it("throws when a brand-new screen entry carries a nonzero count", () => {
    const newBaseline: RatchetBaseline<Dim> = { settings: { alpha: 1, beta: 0 } };
    expect(() => assertBaselineNotRaised(DIMENSIONS, {}, newBaseline, "test-UI", EMPTY)).toThrow(
      /settings.*alpha.*raised from 0 to 1/,
    );
  });

  it("does not throw when a brand-new screen entry is all zeros", () => {
    const newBaseline: RatchetBaseline<Dim> = { settings: { alpha: 0, beta: 0 } };
    expect(() => assertBaselineNotRaised(DIMENSIONS, {}, newBaseline, "test-UI", EMPTY)).not.toThrow();
  });
});

describe("formatComparisonDiff", () => {
  it("reports no regressions when every dimension passes", () => {
    const baseline: RatchetBaseline<Dim> = { home: { alpha: 0, beta: 0 } };
    const result = compareToBaseline(DIMENSIONS, [entry("home", 0, 0)], baseline, EMPTY);
    expect(formatComparisonDiff(result, "test-UI")).toBe("no regressions");
  });

  it("lists every regressed dimension across every screen with the label prefix", () => {
    const baseline: RatchetBaseline<Dim> = {
      home: { alpha: 0, beta: 0 },
      settings: { alpha: 0, beta: 2 },
    };
    const result = compareToBaseline(
      DIMENSIONS,
      [entry("home", 1, 1), entry("settings", 0, 3)],
      baseline,
      EMPTY,
    );
    expect(formatComparisonDiff(result, "test-UI")).toBe(
      "test-UI conformance regressed vs baseline.json: home.alpha: baseline=0 current=1; " +
        "home.beta: baseline=0 current=1; settings.beta: baseline=2 current=3",
    );
  });
});
