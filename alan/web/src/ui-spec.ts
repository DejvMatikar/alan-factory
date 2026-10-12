import uiSpecJson from "./ui-spec.json";

export interface UiSpecReferenceWidth {
  readonly widthPx: number;
  readonly label: string;
  readonly isFloor: boolean;
}

export interface UiSpec {
  readonly tapTarget: {
    readonly minPx: number;
    readonly source: string;
  };
  readonly contrast: {
    readonly normalTextMinRatio: number;
    readonly largeTextMinRatio: number;
    readonly source: string;
  };
  readonly viewport: {
    readonly floorWidthPx: number;
    readonly floorLabel: string;
    readonly referenceWidths: readonly UiSpecReferenceWidth[];
  };
  readonly safeArea: {
    readonly advisory: boolean;
    readonly verifiable: boolean;
    readonly guidance: string;
  };
  readonly desktopAudit: {
    readonly viewport: { readonly widthPx: number; readonly heightPx: number };
    readonly fitHeadroomPx: number;
    readonly anchorTolerancePx: number;
    readonly bandEpsilonPx: number;
    readonly minRepeatingGroupSize: number;
  };
}

export const uiSpec: UiSpec = uiSpecJson;

export const tapTargetMinPx: number = uiSpec.tapTarget.minPx;
export const contrastNormalTextMinRatio: number = uiSpec.contrast.normalTextMinRatio;
export const contrastLargeTextMinRatio: number = uiSpec.contrast.largeTextMinRatio;
export const smallestSupportedViewportWidthPx: number = uiSpec.viewport.floorWidthPx;
export const viewportReferenceWidths: readonly UiSpecReferenceWidth[] = uiSpec.viewport.referenceWidths;
export const safeAreaAdvisory: boolean = uiSpec.safeArea.advisory;
export const safeAreaVerifiable: boolean = uiSpec.safeArea.verifiable;
export const safeAreaGuidance: string = uiSpec.safeArea.guidance;
export const desktopAuditViewportWidthPx: number = uiSpec.desktopAudit.viewport.widthPx;
export const desktopAuditViewportHeightPx: number = uiSpec.desktopAudit.viewport.heightPx;
export const desktopAuditFitHeadroomPx: number = uiSpec.desktopAudit.fitHeadroomPx;
export const desktopAuditAnchorTolerancePx: number = uiSpec.desktopAudit.anchorTolerancePx;
export const desktopAuditBandEpsilonPx: number = uiSpec.desktopAudit.bandEpsilonPx;
export const desktopAuditMinRepeatingGroupSize: number = uiSpec.desktopAudit.minRepeatingGroupSize;
