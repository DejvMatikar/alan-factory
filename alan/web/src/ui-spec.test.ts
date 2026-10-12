import { describe, expect, it } from "vitest";

import {
  contrastLargeTextMinRatio,
  contrastNormalTextMinRatio,
  safeAreaAdvisory,
  safeAreaGuidance,
  safeAreaVerifiable,
  smallestSupportedViewportWidthPx,
  tapTargetMinPx,
  uiSpec,
  viewportReferenceWidths,
} from "./ui-spec";

describe("ui-spec", () => {
  it("defines the minimum tap target size per WCAG 2.5.5 / Apple HIG", () => {
    expect(tapTargetMinPx).toBe(44);
    expect(typeof tapTargetMinPx).toBe("number");
  });

  it("defines the minimum colour contrast ratios per WCAG 2.1 AA", () => {
    expect(contrastNormalTextMinRatio).toBe(4.5);
    expect(typeof contrastNormalTextMinRatio).toBe("number");
    expect(contrastLargeTextMinRatio).toBe(3);
    expect(typeof contrastLargeTextMinRatio).toBe("number");
  });

  it("defines the smallest supported viewport width as the 390px floor", () => {
    expect(smallestSupportedViewportWidthPx).toBe(390);
    expect(typeof smallestSupportedViewportWidthPx).toBe("number");
  });

  it("covers 390px as the gating floor and 402px as a non-gating reference", () => {
    expect(viewportReferenceWidths).toEqual([
      { widthPx: 390, label: "iPhone 16e", isFloor: true },
      { widthPx: 402, label: "iPhone 17", isFloor: false },
    ]);
  });

  it("marks safe-area handling as advisory and explicitly non-verifiable", () => {
    expect(safeAreaAdvisory).toBe(true);
    expect(typeof safeAreaAdvisory).toBe("boolean");
    expect(safeAreaVerifiable).toBe(false);
    expect(typeof safeAreaVerifiable).toBe("boolean");
    expect(typeof safeAreaGuidance).toBe("string");
    expect(safeAreaGuidance.length).toBeGreaterThan(0);
  });

  it("exposes the full spec object matching the JSON source of truth", () => {
    expect(uiSpec.tapTarget.minPx).toBe(tapTargetMinPx);
    expect(uiSpec.contrast.normalTextMinRatio).toBe(contrastNormalTextMinRatio);
    expect(uiSpec.contrast.largeTextMinRatio).toBe(contrastLargeTextMinRatio);
    expect(uiSpec.viewport.floorWidthPx).toBe(smallestSupportedViewportWidthPx);
    expect(uiSpec.safeArea.advisory).toBe(safeAreaAdvisory);
    expect(uiSpec.safeArea.verifiable).toBe(safeAreaVerifiable);
  });
});
