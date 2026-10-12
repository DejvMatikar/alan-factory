import type { ActionGroupCandidate, GeometryNode, GeometryTree, RepeatingGroupCandidate } from "./geometry";

export interface DesktopAuditSpec {
  readonly fitHeadroomPx: number;
  readonly anchorTolerancePx: number;
  readonly bandEpsilonPx: number;
  readonly minRepeatingGroupSize: number;
}

export interface ActionGroupFitResult {
  readonly pass: boolean;
  readonly violations: readonly string[];
}

export interface CrossAxisAlignmentResult {
  readonly pass: boolean;
  readonly violations: readonly string[];
}

export type DesktopAuditState = "standard" | "max";

export interface DesktopScreenReport {
  readonly name: string;
  readonly state: DesktopAuditState;
  readonly route: string;
  readonly actionGroupFit: ActionGroupFitResult;
  readonly crossAxisAlignment: CrossAxisAlignmentResult;
}

function bandsOf(children: readonly GeometryNode[], epsilonPx: number): GeometryNode[][] {
  const sorted = [...children].sort((a, b) => a.rect.y - b.rect.y);
  const bands: GeometryNode[][] = [];
  for (const child of sorted) {
    const band = bands.find((b) => Math.abs(b[0]!.rect.y - child.rect.y) <= epsilonPx);
    if (band) {
      band.push(child);
    } else {
      bands.push([child]);
    }
  }
  return bands;
}

// A child's own border box can be inflated well past its interactive
// control by secondary content stacked beneath it — e.g. a button sitting
// above a long caption sentence, where the composite's rendered width
// tracks the caption's width, not the button's. The caption is free to
// keep wrapping on its own line and shouldn't count against the row's
// single-line fit; the child's firstTextRect (already extracted for
// cross-axis alignment) lands on that leading control in practice, since
// it resolves depth-first and a control's own label is its first
// text-bearing descendant. Fall back to the full rect when no text
// descendant was found at all.
function fitWidthOf(child: GeometryNode): number {
  return child.firstTextRect?.width ?? child.rect.width;
}

function wouldFitOnOneLine(container: ActionGroupCandidate, spec: DesktopAuditSpec): boolean {
  const totalWidth =
    container.children.reduce((sum, child) => sum + fitWidthOf(child), 0) +
    container.columnGapPx * Math.max(0, container.children.length - 1);
  return totalWidth <= container.contentWidthPx - spec.fitHeadroomPx;
}

// Only the deepest (innermost) violating container is counted: a nested flex
// container inside another violating one describes the same visible defect
// twice, and the baseline must tighten toward a single, legible count per
// actual layout site.
function keepInnermostOnly(paths: readonly string[]): string[] {
  return paths.filter((path) => !paths.some((other) => other !== path && other.startsWith(`${path} > `)));
}

export function gradeActionGroupFit(
  actionGroups: readonly ActionGroupCandidate[],
  spec: DesktopAuditSpec,
): ActionGroupFitResult {
  const wrapped = actionGroups.filter(
    (container) => bandsOf(container.children, spec.bandEpsilonPx).length > 1,
  );
  const violating = wrapped.filter((container) => wouldFitOnOneLine(container, spec));
  const innermost = keepInnermostOnly(violating.map((container) => container.path));
  return { pass: innermost.length === 0, violations: innermost };
}

function anchorOffset(child: GeometryNode, rowTopPx: number): number | null {
  return child.firstTextRect === null ? null : child.firstTextRect.y - rowTopPx;
}

function rowMisaligned(row: readonly GeometryNode[], tolerancePx: number): boolean {
  if (row.length < 2) return false;
  const rowTopPx = Math.min(...row.map((child) => child.rect.y));
  const offsets = row
    .map((child) => anchorOffset(child, rowTopPx))
    .filter((offset): offset is number => offset !== null);
  if (offsets.length < 2) return false;
  return Math.max(...offsets) - Math.min(...offsets) > tolerancePx;
}

export function gradeCrossAxisAlignment(
  repeatingGroups: readonly RepeatingGroupCandidate[],
  spec: DesktopAuditSpec,
): CrossAxisAlignmentResult {
  const violations = repeatingGroups
    .filter((group) => group.rows.length >= spec.minRepeatingGroupSize)
    .filter((group) => group.rows.some((row) => rowMisaligned(row, spec.anchorTolerancePx)))
    .map((group) => group.path);
  return { pass: violations.length === 0, violations };
}

export function buildDesktopScreenReport(
  name: string,
  state: DesktopAuditState,
  route: string,
  tree: GeometryTree,
  spec: DesktopAuditSpec,
): DesktopScreenReport {
  return {
    name,
    state,
    route,
    actionGroupFit: gradeActionGroupFit(tree.actionGroups, spec),
    crossAxisAlignment: gradeCrossAxisAlignment(tree.repeatingGroups, spec),
  };
}
