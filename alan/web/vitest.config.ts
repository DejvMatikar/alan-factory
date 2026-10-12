import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Fleet worker gate runs (alan/fleet_load.py, cap 2) and the CI test-frontend
// job (.github/workflows/tests.yml, cap 6) set VITEST_MAX_WORKERS to bound
// this suite's parallelism; unset (human local runs) resolves to undefined,
// which Vitest treats identically to the option being absent.
function resolveMaxWorkers(): number | undefined {
  const raw = process.env.VITEST_MAX_WORKERS;
  if (!raw) return undefined;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    restoreMocks: true,
    clearMocks: true,
    unstubGlobals: true,
    unstubEnvs: true,
    maxWorkers: resolveMaxWorkers(),
    setupFiles: ["./src/test/setup.ts"],
    // .reversed-shims/ is scripts/generate-reversed-shims.mjs's gitignored
    // output (see playwright.reversed.config.ts) — each shim re-exports a
    // real e2e spec via a bare `import`, so if it's ever present on disk when
    // vitest's default include glob scans for *.spec.ts, Vitest tries to
    // execute Playwright's test()/test.describe() and fails loudly. Excluding
    // it here means a local `npm run e2e:reversed` followed by `npm test` in
    // the same working tree can't collide.
    exclude: ["node_modules", "dist", "e2e/**", ".reversed-shims/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary", "json", "lcov", "json-summary"],
      reportOnFailure: true,
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/test/**",
        "src/main.tsx",
        "src/api/schema.ts",
      ],
      thresholds: {
        statements: 100,
        branches: 100,
        functions: 100,
        lines: 100,
      },
    },
  },
});
