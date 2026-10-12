export interface OverflowResult {
  readonly pass: boolean;
  readonly viewportWidthPx: number;
  readonly documentWidthPx: number;
  readonly overflowPx: number;
}

export function gradeOverflow(
  documentWidthPx: number,
  viewportWidthPx: number,
): OverflowResult {
  const overflowPx = Math.max(0, documentWidthPx - viewportWidthPx);
  return { pass: overflowPx === 0, viewportWidthPx, documentWidthPx, overflowPx };
}

export interface TapTargetElement {
  readonly selector: string;
  readonly widthPx: number;
  readonly heightPx: number;
}

export interface TapTargetResult {
  readonly pass: boolean;
  readonly minPx: number;
  readonly checkedCount: number;
  readonly violations: readonly TapTargetElement[];
}

export function gradeTapTargets(
  elements: readonly TapTargetElement[],
  minPx: number,
): TapTargetResult {
  const violations = elements.filter(
    (el) => Math.round(el.widthPx) < minPx || Math.round(el.heightPx) < minPx,
  );
  return {
    pass: violations.length === 0,
    minPx,
    checkedCount: elements.length,
    violations,
  };
}

export interface AccessibilityViolationNode {
  readonly target: readonly string[];
  readonly failureSummary: string | null;
}

export interface AccessibilityViolation {
  readonly id: string;
  readonly impact: string | null;
  readonly description: string;
  readonly nodes: readonly AccessibilityViolationNode[];
}

export interface AccessibilityResult {
  readonly pass: boolean;
  readonly violationCount: number;
  readonly violations: readonly AccessibilityViolation[];
}

export function gradeAccessibility(
  violations: readonly AccessibilityViolation[],
): AccessibilityResult {
  return {
    pass: violations.length === 0,
    violationCount: violations.length,
    violations,
  };
}

export interface ScreenAuditInput {
  readonly name: string;
  readonly route: string;
  readonly documentWidthPx: number;
  readonly tapTargetElements: readonly TapTargetElement[];
  readonly accessibilityViolations: readonly AccessibilityViolation[];
  readonly screenshotPath: string;
}

export interface ScreenAuditSpec {
  readonly viewportWidthPx: number;
  readonly tapTargetMinPx: number;
}

export interface ScreenAuditReport {
  readonly name: string;
  readonly route: string;
  readonly overflow: OverflowResult;
  readonly tapTargets: TapTargetResult;
  readonly accessibility: AccessibilityResult;
  readonly screenshotPath: string;
}

export function buildScreenReport(
  input: ScreenAuditInput,
  spec: ScreenAuditSpec,
): ScreenAuditReport {
  return {
    name: input.name,
    route: input.route,
    overflow: gradeOverflow(input.documentWidthPx, spec.viewportWidthPx),
    tapTargets: gradeTapTargets(input.tapTargetElements, spec.tapTargetMinPx),
    accessibility: gradeAccessibility(input.accessibilityViolations),
    screenshotPath: input.screenshotPath,
  };
}

export interface MobileAuditReportSpec {
  readonly viewportWidthPx: number;
  readonly tapTargetMinPx: number;
  readonly contrastNormalTextMinRatio: number;
  readonly contrastLargeTextMinRatio: number;
}

export interface MobileAuditReport {
  readonly generatedAt: string;
  readonly spec: MobileAuditReportSpec;
  readonly screens: readonly ScreenAuditReport[];
}

export function buildMobileAuditReport(
  screens: readonly ScreenAuditReport[],
  spec: MobileAuditReportSpec,
  generatedAt: string,
): MobileAuditReport {
  return { generatedAt, spec, screens };
}
