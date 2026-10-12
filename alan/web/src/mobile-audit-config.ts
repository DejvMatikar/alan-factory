import os from "node:os";
import path from "node:path";

import { resolveCached, resolveCachedPort, resolveCachedPortAvoiding } from "./lib/derived-run-config";
import type { PortOps } from "./port-avoidance";

export function resolveMobileAuditPort(): number {
  return resolveCachedPort("ALAN_MOBILE_AUDIT_PORT", 5700);
}

export function reserveMobileAuditPort(ops: PortOps): number {
  return resolveCachedPortAvoiding("ALAN_MOBILE_AUDIT_PORT", 5700, ops);
}

export function deriveMobileAuditRoot(pid: number): string {
  return path.join(os.tmpdir(), "alan-mobile-audit", `pid-${pid}`);
}

export function resolveMobileAuditRoot(): string {
  return resolveCached("ALAN_MOBILE_AUDIT_ROOT", () => deriveMobileAuditRoot(process.pid));
}
