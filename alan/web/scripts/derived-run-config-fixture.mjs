// Executable isolation proof for src/lib/derived-run-config.ts's resolveCachedPort,
// run as a REAL separate `node` process (not vitest-imported) so
// derived-run-config.test.ts can assert the cross-process env-inheritance
// contract Playwright's worker forking actually relies on: workers spawn
// with `env: {...process.env, ...extraEnv}`, inheriting whatever the root
// process already cached.
//
// This imports derived-run-config.ts directly rather than mobile-audit-config.ts
// or capture-config.ts: those two use bundler-style extensionless sibling
// imports (`from "./lib/derived-run-config"`, matching this repo's
// moduleResolution: "bundler" convention), which Node's native ESM resolver
// cannot follow without a loader this fixture deliberately doesn't add.
// derived-run-config.ts and its transitive imports (port-avoidance.ts,
// restricted-ports.ts) all use explicit .ts specifiers, which is why a plain
// `node --experimental-strip-types` invocation can load this whole chain.
import { resolveCachedPort } from "../src/lib/derived-run-config.ts";

const [, , envKey, basePortArg] = process.argv;
const port = resolveCachedPort(envKey, Number(basePortArg));
console.log(JSON.stringify({ port, pid: process.pid }));
