import os from "node:os";
import path from "node:path";

import { resolveCached, resolveCachedPort, resolveCachedPortAvoiding } from "./lib/derived-run-config";
import type { PortOps } from "./port-avoidance";

function deriveDesktopAuditRoot(pid: number, suffix: "standard" | "max"): string {
  return path.join(os.tmpdir(), "alan-desktop-audit", suffix, `pid-${pid}`);
}

export function deriveDesktopAuditStandardRoot(pid: number): string {
  return deriveDesktopAuditRoot(pid, "standard");
}

export function deriveDesktopAuditMaxRoot(pid: number): string {
  return deriveDesktopAuditRoot(pid, "max");
}

export function resolveDesktopAuditStandardPort(): number {
  return resolveCachedPort("ALAN_DESKTOP_AUDIT_STANDARD_PORT", 5720);
}

export function reserveDesktopAuditStandardPort(ops: PortOps): number {
  return resolveCachedPortAvoiding("ALAN_DESKTOP_AUDIT_STANDARD_PORT", 5720, ops);
}

export function resolveDesktopAuditStandardRoot(): string {
  return resolveCached("ALAN_DESKTOP_AUDIT_STANDARD_ROOT", () =>
    deriveDesktopAuditStandardRoot(process.pid),
  );
}

export function resolveDesktopAuditMaxPort(): number {
  return resolveCachedPort("ALAN_DESKTOP_AUDIT_MAX_PORT", 5740);
}

export function reserveDesktopAuditMaxPort(ops: PortOps): number {
  return resolveCachedPortAvoiding("ALAN_DESKTOP_AUDIT_MAX_PORT", 5740, ops);
}

export function resolveDesktopAuditMaxRoot(): string {
  return resolveCached("ALAN_DESKTOP_AUDIT_MAX_ROOT", () => deriveDesktopAuditMaxRoot(process.pid));
}
