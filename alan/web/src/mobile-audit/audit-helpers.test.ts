import { describe, expect, it } from "vitest";

import {
  buildMobileAuditReport,
  buildScreenReport,
  gradeAccessibility,
  gradeOverflow,
  gradeTapTargets,
  type AccessibilityViolation,
  type TapTargetElement,
} from "./audit-helpers";
import {
  contrastLargeTextMinRatio,
  contrastNormalTextMinRatio,
  smallestSupportedViewportWidthPx,
  tapTargetMinPx,
} from "../ui-spec";

describe("gradeOverflow", () => {
  it("passes when the document is no wider than the viewport", () => {
    expect(gradeOverflow(390, 390)).toEqual({
      pass: true,
      viewportWidthPx: 390,
      documentWidthPx: 390,
      overflowPx: 0,
    });
  });

  it("clamps a narrower document to zero overflow rather than a negative value", () => {
    expect(gradeOverflow(300, 390)).toEqual({
      pass: true,
      viewportWidthPx: 390,
      documentWidthPx: 300,
      overflowPx: 0,
    });
  });

  it("fails and reports the overflow amount when the document is wider than the viewport", () => {
    expect(gradeOverflow(420, 390)).toEqual({
      pass: false,
      viewportWidthPx: 390,
      documentWidthPx: 420,
      overflowPx: 30,
    });
  });
});

describe("gradeTapTargets", () => {
  it("passes when every element meets the minimum size", () => {
    const elements: TapTargetElement[] = [
      { selector: "button:nth-of-type(1)", widthPx: 44, heightPx: 44 },
      { selector: "a:nth-of-type(1)", widthPx: 60, heightPx: 48 },
    ];
    const result = gradeTapTargets(elements, 44);
    expect(result.pass).toBe(true);
    expect(result.checkedCount).toBe(2);
    expect(result.violations).toEqual([]);
  });

  it("flags an element that is too narrow", () => {
    const elements: TapTargetElement[] = [
      { selector: "button:nth-of-type(1)", widthPx: 30, heightPx: 44 },
    ];
    const result = gradeTapTargets(elements, 44);
    expect(result.pass).toBe(false);
    expect(result.violations).toEqual(elements);
  });

  it("flags an element that is too short", () => {
    const elements: TapTargetElement[] = [
      { selector: "button:nth-of-type(1)", widthPx: 44, heightPx: 20 },
    ];
    const result = gradeTapTargets(elements, 44);
    expect(result.pass).toBe(false);
    expect(result.violations).toEqual(elements);
  });

  it("does not flag an element that is a sub-pixel fraction under the minimum", () => {
    const elements: TapTargetElement[] = [
      { selector: "a:nth-of-type(1)", widthPx: 44, heightPx: 43.99 },
    ];
    const result = gradeTapTargets(elements, 44);
    expect(result.pass).toBe(true);
    expect(result.violations).toEqual([]);
  });

  it("still flags an element rounding below the minimum", () => {
    const elements: TapTargetElement[] = [
      { selector: "button:nth-of-type(1)", widthPx: 44, heightPx: 43.4 },
    ];
    const result = gradeTapTargets(elements, 44);
    expect(result.pass).toBe(false);
    expect(result.violations).toEqual(elements);
  });

  it("passes trivially when there are no interactive elements to check", () => {
    const result = gradeTapTargets([], 44);
    expect(result.pass).toBe(true);
    expect(result.checkedCount).toBe(0);
  });
});

describe("gradeAccessibility", () => {
  it("passes when there are no violations", () => {
    const result = gradeAccessibility([]);
    expect(result).toEqual({ pass: true, violationCount: 0, violations: [] });
  });

  it("fails and carries the violation list through when violations are present", () => {
    const violations: AccessibilityViolation[] = [
      {
        id: "color-contrast",
        impact: "serious",
        description: "Elements must meet minimum color contrast ratio thresholds",
        nodes: [{ target: ["button.primary"], failureSummary: "Fix contrast" }],
      },
    ];
    const result = gradeAccessibility(violations);
    expect(result.pass).toBe(false);
    expect(result.violationCount).toBe(1);
    expect(result.violations).toEqual(violations);
  });
});

describe("buildScreenReport", () => {
  it("composes overflow, tap-target, and accessibility grades for a screen", () => {
    const report = buildScreenReport(
      {
        name: "home",
        route: "/",
        documentWidthPx: 390,
        tapTargetElements: [
          { selector: "button:nth-of-type(1)", widthPx: 44, heightPx: 44 },
        ],
        accessibilityViolations: [],
        screenshotPath: "home.png",
      },
      { viewportWidthPx: 390, tapTargetMinPx: 44 },
    );

    expect(report.name).toBe("home");
    expect(report.route).toBe("/");
    expect(report.screenshotPath).toBe("home.png");
    expect(report.overflow.pass).toBe(true);
    expect(report.tapTargets.pass).toBe(true);
    expect(report.accessibility.pass).toBe(true);
  });
});

describe("buildMobileAuditReport", () => {
  it("wraps per-screen reports with the echoed spec and a timestamp", () => {
    const screenReport = buildScreenReport(
      {
        name: "home",
        route: "/",
        documentWidthPx: 420,
        tapTargetElements: [],
        accessibilityViolations: [],
        screenshotPath: "home.png",
      },
      {
        viewportWidthPx: smallestSupportedViewportWidthPx,
        tapTargetMinPx,
      },
    );

    const spec = {
      viewportWidthPx: smallestSupportedViewportWidthPx,
      tapTargetMinPx,
      contrastNormalTextMinRatio,
      contrastLargeTextMinRatio,
    };

    const report = buildMobileAuditReport([screenReport], spec, "2026-07-03T00:00:00.000Z");

    expect(report.generatedAt).toBe("2026-07-03T00:00:00.000Z");
    expect(report.spec).toEqual(spec);
    expect(report.screens).toEqual([screenReport]);
  });
});
