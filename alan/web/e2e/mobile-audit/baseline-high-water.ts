import type { MobileAuditBaseline } from "../../src/mobile-audit/baseline-compare";

export const MOBILE_AUDIT_BASELINE_HIGH_WATER: MobileAuditBaseline = {
  home: { overflow: 1, tapTarget: 2, contrast: 0 },
  projects: { overflow: 0, tapTarget: 1, contrast: 0 },
  build: { overflow: 0, tapTarget: 4, contrast: 0 },
  health: { overflow: 0, tapTarget: 3, contrast: 0 },
  "explore-architecture": { overflow: 0, tapTarget: 4, contrast: 0 },
  "workspace-decision": { overflow: 0, tapTarget: 8, contrast: 1 },
  "prep-chat": { overflow: 0, tapTarget: 1, contrast: 0 },
  "repo-engine-config": { overflow: 0, tapTarget: 0, contrast: 0 },
  agents: { overflow: 0, tapTarget: 0, contrast: 0 },
  "agents-expanded": { overflow: 0, tapTarget: 0, contrast: 0 },
  "connect-wizard-step1": { overflow: 0, tapTarget: 0, contrast: 0 },
  "connect-wizard-step2": { overflow: 0, tapTarget: 0, contrast: 0 },
  "connect-wizard-step3": { overflow: 0, tapTarget: 0, contrast: 0 },
  "connect-wizard-step4": { overflow: 0, tapTarget: 4, contrast: 0 },
  "repo-rail-collapsed": { overflow: 1, tapTarget: 2, contrast: 0 },
  "repo-drawer-open": { overflow: 1, tapTarget: 2, contrast: 0 },
  operator: { overflow: 0, tapTarget: 0, contrast: 0 },
};
