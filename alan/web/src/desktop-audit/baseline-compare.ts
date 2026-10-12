import type { DesktopScreenReport } from "./audit-helpers";
import {
  assertBaselineNotRaised as assertBaselineNotRaisedGeneric,
  compareToBaseline as compareToBaselineGeneric,
  type BaselineComparisonResult as GenericBaselineComparisonResult,
} from "../ui-audit/baseline-ratchet";

export type DesktopAuditDimension = "actionGroupFit" | "crossAxisAlignment";

const DIMENSIONS: readonly DesktopAuditDimension[] = ["actionGroupFit", "crossAxisAlignment"];
const LABEL_PREFIX = "desktop-UI";

export interface DesktopScreenBaselineCounts {
  readonly actionGroupFit: number;
  readonly crossAxisAlignment: number;
}

export type DesktopAuditBaseline = Readonly<Record<string, DesktopScreenBaselineCounts>>;

const EMPTY_SCREEN_BASELINE: DesktopScreenBaselineCounts = {
  actionGroupFit: 0,
  crossAxisAlignment: 0,
};

export type BaselineComparisonResult = GenericBaselineComparisonResult<DesktopAuditDimension>;

// Every screen is graded in two states (standard/max) against the same
// screen registry (screens-states.ts), so a screen name alone can't key the
// baseline — the "@" separator is safe because registry.ts's screen-name
// pattern forbids "@" in a screen name.
export function desktopBaselineKey(name: string, state: "standard" | "max"): string {
  return `${name}@${state}`;
}

function currentCounts(screen: DesktopScreenReport): DesktopScreenBaselineCounts {
  return {
    actionGroupFit: screen.actionGroupFit.violations.length,
    crossAxisAlignment: screen.crossAxisAlignment.violations.length,
  };
}

export function compareToBaseline(
  screens: readonly DesktopScreenReport[],
  baseline: DesktopAuditBaseline,
): BaselineComparisonResult {
  const entries = screens.map((screen) => ({
    name: desktopBaselineKey(screen.name, screen.state),
    counts: currentCounts(screen),
  }));
  return compareToBaselineGeneric(DIMENSIONS, entries, baseline, EMPTY_SCREEN_BASELINE);
}

export function assertBaselineNotRaised(
  oldBaseline: DesktopAuditBaseline,
  newBaseline: DesktopAuditBaseline,
): void {
  assertBaselineNotRaisedGeneric(DIMENSIONS, oldBaseline, newBaseline, LABEL_PREFIX, EMPTY_SCREEN_BASELINE);
}
