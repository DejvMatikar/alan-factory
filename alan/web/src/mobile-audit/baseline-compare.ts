import type { MobileAuditReport, ScreenAuditReport } from "./audit-helpers";
import {
  assertBaselineNotRaised as assertBaselineNotRaisedGeneric,
  compareToBaseline as compareToBaselineGeneric,
  type DimensionComparison as GenericDimensionComparison,
} from "../ui-audit/baseline-ratchet";

export type MobileAuditDimension = "overflow" | "tapTarget" | "contrast";

const DIMENSIONS: readonly MobileAuditDimension[] = ["overflow", "tapTarget", "contrast"];
const LABEL_PREFIX = "mobile-UI";

export interface ScreenBaselineCounts {
  readonly overflow: number;
  readonly tapTarget: number;
  readonly contrast: number;
}

export type MobileAuditBaseline = Readonly<Record<string, ScreenBaselineCounts>>;

const EMPTY_SCREEN_BASELINE: ScreenBaselineCounts = {
  overflow: 0,
  tapTarget: 0,
  contrast: 0,
};

// A plain alias, not a re-export: ScreenComparison.dimensions below is a
// genuine in-file use of the name, which is what knip's
// ignoreExportsUsedInFile exemption needs. An empty-body `extends`
// interface here trips eslint's no-empty-object-type instead.
export type DimensionComparison = GenericDimensionComparison<MobileAuditDimension>;
export interface ScreenComparison {
  readonly name: string;
  readonly pass: boolean;
  readonly dimensions: readonly DimensionComparison[];
}
export interface BaselineComparisonResult {
  readonly pass: boolean;
  readonly screens: readonly ScreenComparison[];
}

function currentCounts(screen: ScreenAuditReport): ScreenBaselineCounts {
  return {
    overflow: screen.overflow.pass ? 0 : 1,
    tapTarget: screen.tapTargets.violations.length,
    contrast: screen.accessibility.violationCount,
  };
}

export function compareToBaseline(
  report: MobileAuditReport,
  baseline: MobileAuditBaseline,
): BaselineComparisonResult {
  const screens = report.screens.map((screen) => ({ name: screen.name, counts: currentCounts(screen) }));
  return compareToBaselineGeneric(DIMENSIONS, screens, baseline, EMPTY_SCREEN_BASELINE);
}

export function assertBaselineNotRaised(
  oldBaseline: MobileAuditBaseline,
  newBaseline: MobileAuditBaseline,
): void {
  assertBaselineNotRaisedGeneric(DIMENSIONS, oldBaseline, newBaseline, LABEL_PREFIX, EMPTY_SCREEN_BASELINE);
}
