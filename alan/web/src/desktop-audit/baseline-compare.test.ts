import { describe, expect, it } from "vitest";

import {
  assertBaselineNotRaised,
  compareToBaseline,
  desktopBaselineKey,
  type DesktopAuditBaseline,
} from "./baseline-compare";
import type { DesktopScreenReport } from "./audit-helpers";

function report(
  name: string,
  state: "standard" | "max",
  actionGroupFitViolations: readonly string[],
  crossAxisViolations: readonly string[],
): DesktopScreenReport {
  return {
    name,
    state,
    route: "/",
    actionGroupFit: { pass: actionGroupFitViolations.length === 0, violations: actionGroupFitViolations },
    crossAxisAlignment: { pass: crossAxisViolations.length === 0, violations: crossAxisViolations },
  };
}

describe("desktopBaselineKey", () => {
  it("joins screen name and state with '@'", () => {
    expect(desktopBaselineKey("home", "standard")).toBe("home@standard");
    expect(desktopBaselineKey("home", "max")).toBe("home@max");
  });
});

describe("compareToBaseline", () => {
  it("passes when current counts stay at or below the baseline, keyed per screen@state", () => {
    const baseline: DesktopAuditBaseline = {
      "home@standard": { actionGroupFit: 1, crossAxisAlignment: 1 },
    };
    const result = compareToBaseline([report("home", "standard", ["div.a"], ["tr.row"])], baseline);
    expect(result.pass).toBe(true);
    expect(result.screens[0]?.name).toBe("home@standard");
  });

  it("grandfathers a screen@state pair missing from the baseline as zero-tolerance", () => {
    const result = compareToBaseline([report("home", "max", [], [])], {});
    expect(result.pass).toBe(true);
  });

  it("fails when a screen@state pair regresses beyond a zero baseline", () => {
    const result = compareToBaseline([report("home", "standard", ["div.a"], [])], {});
    expect(result.pass).toBe(false);
  });

  it("keeps standard and max states of the same screen independently comparable", () => {
    const baseline: DesktopAuditBaseline = {
      "home@standard": { actionGroupFit: 0, crossAxisAlignment: 0 },
      "home@max": { actionGroupFit: 1, crossAxisAlignment: 0 },
    };
    const result = compareToBaseline(
      [report("home", "standard", [], []), report("home", "max", ["div.a"], [])],
      baseline,
    );
    expect(result.pass).toBe(true);
  });
});

describe("assertBaselineNotRaised", () => {
  it("throws with the desktop-UI label prefix when a dimension is raised", () => {
    const oldBaseline: DesktopAuditBaseline = {
      "home@standard": { actionGroupFit: 0, crossAxisAlignment: 0 },
    };
    const newBaseline: DesktopAuditBaseline = {
      "home@standard": { actionGroupFit: 1, crossAxisAlignment: 0 },
    };
    expect(() => assertBaselineNotRaised(oldBaseline, newBaseline)).toThrow(
      /^desktop-UI baseline regression: screen "home@standard" dimension "actionGroupFit" raised from 0 to 1/,
    );
  });

  it("does not throw when counts are unchanged or lowered", () => {
    const baseline: DesktopAuditBaseline = {
      "home@standard": { actionGroupFit: 1, crossAxisAlignment: 1 },
    };
    expect(() => assertBaselineNotRaised(baseline, baseline)).not.toThrow();
    const lowered: DesktopAuditBaseline = {
      "home@standard": { actionGroupFit: 0, crossAxisAlignment: 1 },
    };
    expect(() => assertBaselineNotRaised(baseline, lowered)).not.toThrow();
  });
});
