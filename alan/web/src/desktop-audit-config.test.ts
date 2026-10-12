import os from "node:os";
import path from "node:path";

import { afterEach, expect, it } from "vitest";

import {
  deriveDesktopAuditMaxRoot,
  deriveDesktopAuditStandardRoot,
  reserveDesktopAuditMaxPort,
  reserveDesktopAuditStandardPort,
  resolveDesktopAuditMaxPort,
  resolveDesktopAuditMaxRoot,
  resolveDesktopAuditStandardPort,
  resolveDesktopAuditStandardRoot,
} from "./desktop-audit-config";
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

interface Fixture {
  readonly portEnvKey: string;
  readonly rootEnvKey: string;
  readonly basePort: number;
  readonly resolvePort: () => number;
  readonly reservePort: (ops: PortOps) => number;
  readonly resolveRoot: () => string;
  readonly deriveRoot: (pid: number) => string;
}

const FIXTURES: readonly Fixture[] = [
  {
    portEnvKey: "ALAN_DESKTOP_AUDIT_STANDARD_PORT",
    rootEnvKey: "ALAN_DESKTOP_AUDIT_STANDARD_ROOT",
    basePort: 5720,
    resolvePort: resolveDesktopAuditStandardPort,
    reservePort: reserveDesktopAuditStandardPort,
    resolveRoot: resolveDesktopAuditStandardRoot,
    deriveRoot: deriveDesktopAuditStandardRoot,
  },
  {
    portEnvKey: "ALAN_DESKTOP_AUDIT_MAX_PORT",
    rootEnvKey: "ALAN_DESKTOP_AUDIT_MAX_ROOT",
    basePort: 5740,
    resolvePort: resolveDesktopAuditMaxPort,
    reservePort: reserveDesktopAuditMaxPort,
    resolveRoot: resolveDesktopAuditMaxRoot,
    deriveRoot: deriveDesktopAuditMaxRoot,
  },
];

const ENV_KEYS = FIXTURES.flatMap((fixture) => [fixture.portEnvKey, fixture.rootEnvKey]);
const originalEnv = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));

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

for (const fx of FIXTURES) {
  it(`${fx.rootEnvKey}: derives a distinct, tmp-rooted state root per pid, never the homedir-rooted legacy path`, () => {
    expect(fx.deriveRoot(111)).not.toBe(fx.deriveRoot(222));
    expect(fx.deriveRoot(111)).not.toContain(os.homedir());
  });

  it(`${fx.rootEnvKey}: resolves the pre-set env value unchanged on every call`, () => {
    const preset = path.join(os.tmpdir(), `${fx.rootEnvKey}-identity-test-root`);
    process.env[fx.rootEnvKey] = preset;
    expect(fx.resolveRoot()).toBe(preset);
    expect(fx.resolveRoot()).toBe(preset);
  });

  it(`${fx.rootEnvKey}: derives and caches a fresh value when unset`, () => {
    delete process.env[fx.rootEnvKey];
    const resolved = fx.resolveRoot();
    expect(resolved).toBe(fx.deriveRoot(process.pid));
    expect(process.env[fx.rootEnvKey]).toBe(resolved);
  });

  it(`${fx.portEnvKey}: returns the pre-set env value unchanged when it is a valid positive integer`, () => {
    process.env[fx.portEnvKey] = "6321";
    expect(fx.resolvePort()).toBe(6321);
    expect(fx.resolvePort()).toBe(6321);
  });

  it(`${fx.portEnvKey}: derives and caches a fresh port when unset or invalid`, () => {
    process.env[fx.portEnvKey] = "not-a-port";
    const resolved = fx.resolvePort();
    expect(resolved).toBe(candidatePorts(fx.basePort, process.pid)[0]);
    expect(process.env[fx.portEnvKey]).toBe(String(resolved));
  });

  it(`${fx.portEnvKey}: reserve claims the first candidate and caches it when free`, () => {
    delete process.env[fx.portEnvKey];
    const resolved = fx.reservePort(fakePortOps(new Map()));
    expect(resolved).toBe(candidatePorts(fx.basePort, process.pid)[0]);
    expect(process.env[fx.portEnvKey]).toBe(String(resolved));
  });

  it(`${fx.portEnvKey}: reserve advances past a foreign holder on the first candidate`, () => {
    delete process.env[fx.portEnvKey];
    const [first, second] = candidatePorts(fx.basePort, process.pid);
    const ops = fakePortOps(new Map([[first ?? 0, { pid: 12345, command: "Rancher" }]]));
    const resolved = fx.reservePort(ops);
    expect(resolved).toBe(second);
    expect(resolved).toBeGreaterThanOrEqual(fx.basePort);
    expect(process.env[fx.portEnvKey]).toBe(String(resolved));
  });
}
