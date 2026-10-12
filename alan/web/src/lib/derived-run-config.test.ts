import { spawn } from "node:child_process";
import path from "node:path";

import { afterEach, expect, it } from "vitest";

import { resolveCached, resolveCachedPort, resolveCachedPortAvoiding } from "./derived-run-config";
import { candidatePorts } from "../port-avoidance";
import type { PortOps } from "../port-avoidance";

const ENV_KEY = "DERIVED_RUN_CONFIG_TEST_KEY";
const PORT_ENV_KEY = "DERIVED_RUN_CONFIG_TEST_PORT_KEY";

function throwingOps(): PortOps {
  return {
    holders: () => {
      throw new Error("ops.holders should not be called when env is already set");
    },
    reclaim: () => {
      throw new Error("ops.reclaim should not be called when env is already set");
    },
    warn: () => {
      throw new Error("ops.warn should not be called when env is already set");
    },
  };
}

function freeOps(): PortOps {
  return { holders: () => [], reclaim: () => undefined, warn: () => undefined };
}

afterEach(() => {
  delete process.env[ENV_KEY];
  delete process.env[PORT_ENV_KEY];
});

it("resolveCached returns the pre-set env value unchanged and never calls derive", () => {
  process.env[ENV_KEY] = "preset-value";
  const derive = (): string => {
    throw new Error("derive should not run when env is already set");
  };
  expect(resolveCached(ENV_KEY, derive)).toBe("preset-value");
});

it("resolveCached derives and caches a fresh value when unset or whitespace-only", () => {
  process.env[ENV_KEY] = "   ";
  const resolved = resolveCached(ENV_KEY, () => "derived-value");
  expect(resolved).toBe("derived-value");
  expect(process.env[ENV_KEY]).toBe("derived-value");
});

it("resolveCachedPort returns the pre-set env value unchanged when a valid positive integer", () => {
  process.env[PORT_ENV_KEY] = "1234";
  expect(resolveCachedPort(PORT_ENV_KEY, 9000)).toBe(1234);
});

it("resolveCachedPort derives and caches basePort + (pid % 1000) when unset or invalid", () => {
  process.env[PORT_ENV_KEY] = "not-a-port";
  const resolved = resolveCachedPort(PORT_ENV_KEY, 9000);
  expect(resolved).toBe(9000 + (process.pid % 1000));
  expect(process.env[PORT_ENV_KEY]).toBe(String(resolved));
});

it("resolveCachedPort derives candidatePorts(basePort, pid)[0] on a span that can diverge from the raw formula", () => {
  process.env[PORT_ENV_KEY] = "not-a-port";
  const resolved = resolveCachedPort(PORT_ENV_KEY, 5700);
  expect(resolved).toBe(candidatePorts(5700, process.pid)[0]);
  expect(process.env[PORT_ENV_KEY]).toBe(String(resolved));
});

it("resolveCachedPort and resolveCachedPortAvoiding honour a preset value that is itself restricted", () => {
  process.env[PORT_ENV_KEY] = "6667";
  expect(resolveCachedPort(PORT_ENV_KEY, 5700)).toBe(6667);
  expect(resolveCachedPortAvoiding(PORT_ENV_KEY, 5700, throwingOps())).toBe(6667);
});

it("resolveCachedPortAvoiding returns the pre-set env value unchanged and never calls ops", () => {
  process.env[PORT_ENV_KEY] = "4242";
  expect(resolveCachedPortAvoiding(PORT_ENV_KEY, 5700, throwingOps())).toBe(4242);
});

it("resolveCachedPortAvoiding walks and caches the winning port when unset", () => {
  delete process.env[PORT_ENV_KEY];
  const resolved = resolveCachedPortAvoiding(PORT_ENV_KEY, 5700, freeOps());
  expect(resolved).toBe(candidatePorts(5700, process.pid)[0]);
  expect(process.env[PORT_ENV_KEY]).toBe(String(resolved));
});

it("resolveCachedPortAvoiding walks when the existing env value is invalid", () => {
  process.env[PORT_ENV_KEY] = "not-a-port";
  const resolved = resolveCachedPortAvoiding(PORT_ENV_KEY, 5700, freeOps());
  expect(resolved).toBe(candidatePorts(5700, process.pid)[0]);
});

it("documented port ranges (local e2e, capture, mobile-audit, CI e2e) never overlap", () => {
  const ranges: readonly { readonly name: string; readonly min: number; readonly max: number }[] = [
    { name: "local e2e", min: 4300, max: 4303 },
    { name: "screen capture", min: 4700, max: 5699 },
    { name: "mobile audit", min: 5700, max: 6699 },
    { name: "CI e2e", min: 20000, max: 35999 },
  ];

  for (const a of ranges) {
    for (const b of ranges) {
      if (a === b) continue;
      const overlaps = a.min <= b.max && b.min <= a.max;
      expect(overlaps, `${a.name} [${String(a.min)}-${String(a.max)}] overlaps ${b.name} [${String(b.min)}-${String(b.max)}]`).toBe(false);
    }
  }
});

const FIXTURE_PATH = path.resolve(process.cwd(), "scripts", "derived-run-config-fixture.mjs");

interface FixtureResult {
  readonly port: number;
  readonly pid: number;
}

function runFixture(envKey: string, basePort: number, extraEnv: Record<string, string>): Promise<FixtureResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      ["--no-warnings", "--experimental-strip-types", FIXTURE_PATH, envKey, String(basePort)],
      { env: { ...process.env, ...extraEnv } },
    );
    let stdout = "";
    child.stdout?.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code !== 0) {
        reject(new Error(`derived-run-config-fixture.mjs exited with code ${String(code)}`));
        return;
      }
      resolve(JSON.parse(stdout) as FixtureResult);
    });
  });
}

it("two concurrently spawned child processes agree on the cached port when the env var is inherited", async () => {
  const envKey = "DERIVED_RUN_CONFIG_CROSS_PROCESS_TEST_PORT";
  const [a, b] = await Promise.all([
    runFixture(envKey, 9000, { [envKey]: "9123" }),
    runFixture(envKey, 9000, { [envKey]: "9123" }),
  ]);
  expect(a.pid).not.toBe(b.pid);
  expect(a.port).toBe(9123);
  expect(b.port).toBe(9123);
});

it("two concurrently spawned child processes derive their own pid-consistent port when no env var is inherited", async () => {
  const envKey = "DERIVED_RUN_CONFIG_CROSS_PROCESS_TEST_PORT_UNSET";
  const [a, b] = await Promise.all([runFixture(envKey, 9000, {}), runFixture(envKey, 9000, {})]);
  expect(a.pid).not.toBe(b.pid);
  expect(a.port).toBe(9000 + (a.pid % 1000));
  expect(b.port).toBe(9000 + (b.pid % 1000));
});
