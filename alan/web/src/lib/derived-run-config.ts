// Explicit .ts extension (allowImportingTsExtensions in tsconfig.json): this
// module is loaded directly by plain `node --experimental-strip-types` in
// scripts/derived-run-config-fixture.mjs, whose native ESM resolver — unlike
// Vite/tsc's bundler resolution used everywhere else — cannot follow an
// extensionless relative specifier.
import { candidatePorts, firstFreePort, type PortOps } from "../port-avoidance.ts";

// Cached in process.env so every reload of this module (root or worker process, different pids) reuses the same value.
export function resolveCached(envKey: string, derive: () => string): string {
  const fromEnv = process.env[envKey]?.trim();
  if (fromEnv !== undefined && fromEnv !== "") {
    return fromEnv;
  }
  const value = derive();
  process.env[envKey] = value;
  return value;
}

function cachedPortFromEnv(envKey: string): number | undefined {
  const fromEnv = Number(process.env[envKey]);
  if (Number.isInteger(fromEnv) && fromEnv > 0) {
    return fromEnv;
  }
  return undefined;
}

// candidatePorts(basePort, pid)[0]: basePort + (pid % 1000) only when that offset
// is not in RESTRICTED_PORTS, otherwise the next stride offset that isn't; range
// table asserted in derived-run-config.test.ts.
export function resolveCachedPort(envKey: string, basePort: number): number {
  const cached = cachedPortFromEnv(envKey);
  if (cached !== undefined) {
    return cached;
  }
  const port = candidatePorts(basePort, process.pid)[0]!;
  process.env[envKey] = String(port);
  return port;
}

// Same env-preset contract as resolveCachedPort, but on an unset/invalid env
// value it walks candidates via firstFreePort instead of taking basePort +
// (pid % 1000) unconditionally — so a foreign listener already on that port
// cannot collide. A preset env value is honoured verbatim and the walk is
// skipped entirely, so Playwright worker processes that inherit the cached
// value from the root process never re-walk and can only ever agree with it.
export function resolveCachedPortAvoiding(envKey: string, basePort: number, ops: PortOps): number {
  const cached = cachedPortFromEnv(envKey);
  if (cached !== undefined) {
    return cached;
  }
  const port = firstFreePort(basePort, process.pid, ops);
  process.env[envKey] = String(port);
  return port;
}
