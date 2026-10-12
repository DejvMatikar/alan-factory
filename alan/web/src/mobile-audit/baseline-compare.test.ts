import { describe, expect, it } from "vitest";

import { buildScreenReport } from "./audit-helpers";
import {
  assertBaselineNotRaised,
  compareToBaseline,
  type MobileAuditBaseline,
} from "./baseline-compare";
import type { MobileAuditReport, ScreenAuditReport } from "./audit-helpers";

const SPEC = { viewportWidthPx: 390, tapTargetMinPx: 44 };

function reportOf(...screens: readonly ScreenAuditReport[]): MobileAuditReport {
  return {
    generatedAt: "2026-07-03T00:00:00.000Z",
    spec: {
      viewportWidthPx: 390,
      tapTargetMinPx: 44,
      contrastNormalTextMinRatio: 4.5,
      contrastLargeTextMinRatio: 3,
    },
    screens,
  };
}

function cleanScreen(name: string): ScreenAuditReport {
  return buildScreenReport(
    {
      name,
      route: "/",
      documentWidthPx: 390,
      tapTargetElements: [{ selector: "button", widthPx: 44, heightPx: 44 }],
      accessibilityViolations: [],
      screenshotPath: `${name}.png`,
    },
    SPEC,
  );
}

function overflowingScreen(name: string): ScreenAuditReport {
  return buildScreenReport(
    {
      name,
      route: "/",
      documentWidthPx: 480,
      tapTargetElements: [{ selector: "button", widthPx: 44, heightPx: 44 }],
      accessibilityViolations: [],
      screenshotPath: `${name}.png`,
    },
    SPEC,
  );
}

describe("compareToBaseline", () => {
  it("passes when current counts stay at or below the baseline for every dimension", () => {
    const screen = buildScreenReport(
      {
        name: "home",
        route: "/",
        documentWidthPx: 390,
        tapTargetElements: [
          { selector: "a", widthPx: 20, heightPx: 20 },
          { selector: "b", widthPx: 20, heightPx: 20 },
        ],
        accessibilityViolations: [],
        screenshotPath: "home.png",
      },
      SPEC,
    );
    const baseline: MobileAuditBaseline = {
      home: { overflow: 0, tapTarget: 2, contrast: 1 },
    };

    const result = compareToBaseline(reportOf(screen), baseline);

    expect(result.pass).toBe(true);
    expect(result.screens).toEqual([
      {
        name: "home",
        pass: true,
        dimensions: [
          { dimension: "overflow", baselineCount: 0, currentCount: 0, pass: true },
          { dimension: "tapTarget", baselineCount: 2, currentCount: 2, pass: true },
          { dimension: "contrast", baselineCount: 1, currentCount: 0, pass: true },
        ],
      },
    ]);
  });

  it("fails the overflow dimension when a screen newly overflows beyond a zero baseline", () => {
    const baseline: MobileAuditBaseline = {
      home: { overflow: 0, tapTarget: 0, contrast: 0 },
    };

    const result = compareToBaseline(reportOf(overflowingScreen("home")), baseline);

    expect(result.pass).toBe(false);
    expect(result.screens[0]?.pass).toBe(false);
    expect(result.screens[0]?.dimensions[0]).toEqual({
      dimension: "overflow",
      baselineCount: 0,
      currentCount: 1,
      pass: false,
    });
  });

  it("grandfathers a screen missing from the baseline as zero-tolerance and passes it clean", () => {
    const result = compareToBaseline(reportOf(cleanScreen("settings")), {});

    expect(result.pass).toBe(true);
    expect(result.screens[0]?.dimensions.every((d) => d.baselineCount === 0)).toBe(true);
  });

  it("fails a screen missing from the baseline once it carries any violation", () => {
    const result = compareToBaseline(reportOf(overflowingScreen("settings")), {});

    expect(result.pass).toBe(false);
  });

  it("fails overall when any one of several screens regresses", () => {
    const baseline: MobileAuditBaseline = {
      home: { overflow: 0, tapTarget: 0, contrast: 0 },
      settings: { overflow: 0, tapTarget: 0, contrast: 0 },
    };

    const result = compareToBaseline(
      reportOf(cleanScreen("home"), overflowingScreen("settings")),
      baseline,
    );

    expect(result.pass).toBe(false);
    expect(result.screens[0]?.pass).toBe(true);
    expect(result.screens[1]?.pass).toBe(false);
  });
});

describe("assertBaselineNotRaised", () => {
  it("does not throw when counts are unchanged", () => {
    const baseline: MobileAuditBaseline = {
      home: { overflow: 1, tapTarget: 11, contrast: 0 },
    };

    expect(() => assertBaselineNotRaised(baseline, baseline)).not.toThrow();
  });

  it("does not throw when a baseline count is lowered", () => {
    const oldBaseline: MobileAuditBaseline = {
      home: { overflow: 1, tapTarget: 11, contrast: 0 },
    };
    const newBaseline: MobileAuditBaseline = {
      home: { overflow: 1, tapTarget: 9, contrast: 0 },
    };

    expect(() => assertBaselineNotRaised(oldBaseline, newBaseline)).not.toThrow();
  });

  it("throws when a dimension count is raised for an existing screen", () => {
    const oldBaseline: MobileAuditBaseline = {
      home: { overflow: 1, tapTarget: 11, contrast: 0 },
    };
    const newBaseline: MobileAuditBaseline = {
      home: { overflow: 1, tapTarget: 12, contrast: 0 },
    };

    expect(() => assertBaselineNotRaised(oldBaseline, newBaseline)).toThrow(
      /home.*tapTarget.*raised from 11 to 12/,
    );
  });

  it("throws when a brand-new screen entry carries a nonzero count", () => {
    const newBaseline: MobileAuditBaseline = {
      settings: { overflow: 1, tapTarget: 0, contrast: 0 },
    };

    expect(() => assertBaselineNotRaised({}, newBaseline)).toThrow(
      /settings.*overflow.*raised from 0 to 1/,
    );
  });

  it("does not throw when a brand-new screen entry is all zeros", () => {
    const newBaseline: MobileAuditBaseline = {
      settings: { overflow: 0, tapTarget: 0, contrast: 0 },
    };

    expect(() => assertBaselineNotRaised({}, newBaseline)).not.toThrow();
  });

  it("prefixes the thrown message with mobile-UI, proving the generalized core is wired with the right label", () => {
    const oldBaseline: MobileAuditBaseline = { home: { overflow: 0, tapTarget: 0, contrast: 0 } };
    const newBaseline: MobileAuditBaseline = { home: { overflow: 1, tapTarget: 0, contrast: 0 } };
    expect(() => assertBaselineNotRaised(oldBaseline, newBaseline)).toThrow(/^mobile-UI baseline regression:/);
  });
});
