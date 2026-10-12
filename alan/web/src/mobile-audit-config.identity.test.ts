import os from "node:os";
import path from "node:path";

import { afterEach, expect, it } from "vitest";

import { deriveMobileAuditRoot, reserveMobileAuditPort, resolveMobileAuditPort, resolveMobileAuditRoot } from "./mobile-audit-config";
import { candidatePorts } from "./port-avoidance";
import type { PortHolder, PortOps } from "./port-avoidance";

function fakePortOps(blocked: ReadonlyMap<number, PortHolder>): PortOps {
  return {
    holders: (port) => {
      const holder = blocked.get(port);
      return holder ? [holder] : [];
    },
    reclaim: () => undefined,
    warn: () => undefined,
  };
}

const ENV_KEYS = ["ALAN_MOBILE_AUDIT_PORT", "ALAN_MOBILE_AUDIT_ROOT"] as const;

const originalEnv = Object.fromEntries(
  ENV_KEYS.map((key) => [key, process.env[key]]),
) as Record<(typeof ENV_KEYS)[number], string | undefined>;

afterEach(() => {
  for (const key of ENV_KEYS) {
    const original = originalEnv[key];
    if (original === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = original;
    }
  }
});

it("derives a distinct, tmp-rooted state root per pid, never the homedir-rooted legacy path", () => {
  const pidA = 111;
  const pidB = 222;
  expect(deriveMobileAuditRoot(pidA)).not.toBe(deriveMobileAuditRoot(pidB));
  expect(deriveMobileAuditRoot(pidA)).toBe(path.join(os.tmpdir(), "alan-mobile-audit", `pid-${pidA}`));
  expect(deriveMobileAuditRoot(pidA)).not.toBe(path.join(os.homedir(), ".alan-mobile-audit"));
});

it("resolveMobileAuditRoot returns the pre-set env value unchanged on every call", () => {
  const preset = path.join(os.tmpdir(), "mobile-audit-config-identity-test-root");
  process.env.ALAN_MOBILE_AUDIT_ROOT = preset;
  expect(resolveMobileAuditRoot()).toBe(preset);
  expect(resolveMobileAuditRoot()).toBe(preset);
});

it("resolveMobileAuditRoot derives and caches a fresh value when unset", () => {
  delete process.env.ALAN_MOBILE_AUDIT_ROOT;
  const resolved = resolveMobileAuditRoot();
  expect(resolved).toBe(deriveMobileAuditRoot(process.pid));
  expect(process.env.ALAN_MOBILE_AUDIT_ROOT).toBe(resolved);
});

it("resolveMobileAuditRoot derives a fresh value when set to whitespace only", () => {
  process.env.ALAN_MOBILE_AUDIT_ROOT = "   ";
  const resolved = resolveMobileAuditRoot();
  expect(resolved).toBe(deriveMobileAuditRoot(process.pid));
  expect(process.env.ALAN_MOBILE_AUDIT_ROOT).toBe(resolved);
});

it("resolveMobileAuditPort returns the pre-set env value unchanged when it is a valid positive integer", () => {
  process.env.ALAN_MOBILE_AUDIT_PORT = "6321";
  expect(resolveMobileAuditPort()).toBe(6321);
  expect(resolveMobileAuditPort()).toBe(6321);
});

it("resolveMobileAuditPort derives and caches a fresh port when unset or invalid", () => {
  process.env.ALAN_MOBILE_AUDIT_PORT = "not-a-port";
  const resolved = resolveMobileAuditPort();
  expect(resolved).toBe(candidatePorts(5700, process.pid)[0]);
  expect(process.env.ALAN_MOBILE_AUDIT_PORT).toBe(String(resolved));
});

it("reserveMobileAuditPort claims the first candidate and caches it when free", () => {
  delete process.env.ALAN_MOBILE_AUDIT_PORT;
  const resolved = reserveMobileAuditPort(fakePortOps(new Map()));
  expect(resolved).toBe(candidatePorts(5700, process.pid)[0]);
  expect(process.env.ALAN_MOBILE_AUDIT_PORT).toBe(String(resolved));
});

it("reserveMobileAuditPort advances past a foreign holder on the first candidate", () => {
  delete process.env.ALAN_MOBILE_AUDIT_PORT;
  const [first, second] = candidatePorts(5700, process.pid);
  const ops = fakePortOps(new Map([[first ?? 0, { pid: 12345, command: "Rancher" }]]));
  const resolved = reserveMobileAuditPort(ops);
  expect(resolved).toBe(second);
  expect(resolved).toBeGreaterThanOrEqual(5700);
  expect(resolved).toBeLessThanOrEqual(6699);
  expect(process.env.ALAN_MOBILE_AUDIT_PORT).toBe(String(resolved));
});
