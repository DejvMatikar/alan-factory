import { describe, expect, it } from "vitest";

import { buildDesktopScreenReport, gradeActionGroupFit, gradeCrossAxisAlignment } from "./audit-helpers";
import type { DesktopAuditSpec } from "./audit-helpers";
import type { ActionGroupCandidate, GeometryNode, GeometryTree, RepeatingGroupCandidate } from "./geometry";
import {
  desktopAuditAnchorTolerancePx,
  desktopAuditBandEpsilonPx,
  desktopAuditFitHeadroomPx,
  desktopAuditMinRepeatingGroupSize,
  desktopAuditViewportHeightPx,
  desktopAuditViewportWidthPx,
} from "../ui-spec";

const SPEC: DesktopAuditSpec = {
  fitHeadroomPx: 10,
  anchorTolerancePx: 3,
  bandEpsilonPx: 4,
  minRepeatingGroupSize: 3,
};

describe("desktop audit spec constants", () => {
  it("exposes positive, sane geometry tolerances from ui-spec.json", () => {
    expect(desktopAuditViewportWidthPx).toBeGreaterThan(0);
    expect(desktopAuditViewportHeightPx).toBeGreaterThan(0);
    expect(desktopAuditFitHeadroomPx).toBeGreaterThanOrEqual(0);
    expect(desktopAuditAnchorTolerancePx).toBeGreaterThanOrEqual(0);
    expect(desktopAuditBandEpsilonPx).toBeGreaterThanOrEqual(0);
    expect(desktopAuditMinRepeatingGroupSize).toBeGreaterThanOrEqual(2);
  });
});

function node(x: number, y: number, width: number, height: number, textY: number | null = y): GeometryNode {
  return {
    rect: { x, y, width, height },
    firstTextRect: textY === null ? null : { x, y: textY, width, height },
    signature: "span",
  };
}

function actionGroup(
  path: string,
  contentWidthPx: number,
  children: readonly GeometryNode[],
  columnGapPx = 0,
): ActionGroupCandidate {
  return { path, contentWidthPx, columnGapPx, children };
}

describe("gradeActionGroupFit", () => {
  it("passes a container whose children stay on one row band", () => {
    const group = actionGroup("div.a", 300, [node(0, 0, 60, 40), node(60, 0, 60, 40)]);
    const result = gradeActionGroupFit([group], SPEC);
    expect(result.pass).toBe(true);
    expect(result.violations).toEqual([]);
  });

  it("does not flag wrapping that genuinely would not fit on one line", () => {
    const group = actionGroup("div.a", 100, [node(0, 0, 90, 40), node(0, 50, 90, 40)]);
    const result = gradeActionGroupFit([group], SPEC);
    expect(result.pass).toBe(true);
  });

  it("flags wrapping when the children would have fit on one line with headroom to spare", () => {
    const group = actionGroup("div.a", 300, [node(0, 0, 50, 40), node(0, 50, 50, 40)]);
    const result = gradeActionGroupFit([group], SPEC);
    expect(result.pass).toBe(false);
    expect(result.violations).toEqual(["div.a"]);
  });

  it("keeps only the innermost of two nested violating containers", () => {
    const outer = actionGroup("div.a", 300, [node(0, 0, 50, 40), node(0, 50, 50, 40)]);
    const inner = actionGroup("div.a > div.b", 300, [node(0, 0, 50, 40), node(0, 50, 50, 40)]);
    const result = gradeActionGroupFit([outer, inner], SPEC);
    expect(result.violations).toEqual(["div.a > div.b"]);
  });

  it("keeps two separate (non-nested) violating containers", () => {
    const a = actionGroup("div.a", 300, [node(0, 0, 50, 40), node(0, 50, 50, 40)]);
    const c = actionGroup("div.c", 300, [node(0, 0, 50, 40), node(0, 50, 50, 40)]);
    const result = gradeActionGroupFit([a, c], SPEC);
    expect([...result.violations].sort()).toEqual(["div.a", "div.c"]);
  });

  it("passes trivially when there are no action-group candidates", () => {
    expect(gradeActionGroupFit([], SPEC).pass).toBe(true);
  });

  it("flags a control wrapped onto its own line despite spare room, even though its own box (inflated by a caption stacked beneath it) would not fit", () => {
    const control = (controlWidthPx: number, rowY: number): GeometryNode => ({
      rect: { x: 0, y: rowY, width: 400, height: 60 },
      firstTextRect: { x: 0, y: rowY, width: controlWidthPx, height: 20 },
      signature: "span",
    });
    const group = actionGroup("div.a", 500, [control(100, 0), control(80, 80)]);
    const result = gradeActionGroupFit([group], SPEC);
    expect(result.pass).toBe(false);
    expect(result.violations).toEqual(["div.a"]);
  });

  it("falls back to the full rect width when a child has no text descendant to anchor on", () => {
    const group = actionGroup("div.a", 300, [node(0, 0, 50, 40, null), node(0, 50, 50, 40, null)]);
    const result = gradeActionGroupFit([group], SPEC);
    expect(result.pass).toBe(false);
    expect(result.violations).toEqual(["div.a"]);
  });
});

describe("gradeCrossAxisAlignment", () => {
  function group(path: string, rows: readonly (readonly GeometryNode[])[]): RepeatingGroupCandidate {
    return { path, rows };
  }

  it("ignores a group under the minimum repeating-group size even if misaligned", () => {
    const rows = [
      [node(0, 0, 50, 20, 0), node(50, 0, 50, 20, 10)],
      [node(0, 20, 50, 20, 20), node(50, 20, 50, 20, 30)],
    ];
    const result = gradeCrossAxisAlignment([group("tbody > tr.row", rows)], SPEC);
    expect(result.pass).toBe(true);
    expect(result.violations).toEqual([]);
  });

  it("passes a repeating group whose rows all keep columns within tolerance", () => {
    const rows = [
      [node(0, 0, 50, 20, 0), node(50, 0, 50, 20, 1)],
      [node(0, 20, 50, 20, 20), node(50, 20, 50, 20, 21)],
      [node(0, 40, 50, 20, 40), node(50, 40, 50, 20, 41)],
    ];
    const result = gradeCrossAxisAlignment([group("tbody > tr.row", rows)], SPEC);
    expect(result.pass).toBe(true);
  });

  it("flags a repeating group with one row whose columns disagree beyond tolerance", () => {
    const rows = [
      [node(0, 0, 50, 20, 0), node(50, 0, 50, 20, 1)],
      [node(0, 20, 50, 20, 30), node(50, 20, 50, 20, 20)],
      [node(0, 40, 50, 20, 40), node(50, 40, 50, 20, 41)],
    ];
    const result = gradeCrossAxisAlignment([group("tbody > tr.row", rows)], SPEC);
    expect(result.pass).toBe(false);
    expect(result.violations).toEqual(["tbody > tr.row"]);
  });

  it("does not flag a row with fewer than 2 columns", () => {
    const rows = [[node(0, 0, 50, 20, 0)], [node(0, 20, 50, 20, 20)], [node(0, 40, 50, 20, 40)]];
    const result = gradeCrossAxisAlignment([group("tbody > tr.row", rows)], SPEC);
    expect(result.pass).toBe(true);
  });

  it("does not flag a row where fewer than 2 columns carry a text anchor", () => {
    const rows = [
      [node(0, 0, 50, 20, 0), node(50, 0, 50, 20, null)],
      [node(0, 20, 50, 20, 20), node(50, 20, 50, 20, null)],
      [node(0, 40, 50, 20, 40), node(50, 40, 50, 20, null)],
    ];
    const result = gradeCrossAxisAlignment([group("tbody > tr.row", rows)], SPEC);
    expect(result.pass).toBe(true);
  });

  it("passes trivially when there are no repeating-group candidates", () => {
    expect(gradeCrossAxisAlignment([], SPEC).pass).toBe(true);
  });
});

describe("buildDesktopScreenReport", () => {
  it("composes both dimensions with the screen's name/state/route", () => {
    const tree: GeometryTree = { actionGroups: [], repeatingGroups: [] };
    const report = buildDesktopScreenReport("home", "standard", "/", tree, SPEC);
    expect(report).toEqual({
      name: "home",
      state: "standard",
      route: "/",
      actionGroupFit: { pass: true, violations: [] },
      crossAxisAlignment: { pass: true, violations: [] },
    });
  });
});
