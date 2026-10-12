import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

// Secondary evidence for T2-11's reversed-order requirement, not the primary
// isolation proof (that's scripts/e2e-isolation-sweep.mjs). With workers: 4
// and fullyParallel: false, the main suite's "run order" is several
// dynamically-fed queues whose composition shifts every run — a reversed
// pass through that samples one point in an already-nondeterministic space.
// workers: 1 here makes "reversed" literally true at the cost of changing
// the timing profile versus the real CI configuration; note both facts
// wherever this run's output is cited.
const here = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  testDir: "./.reversed-shims",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  globalTeardown: "./e2e/global-teardown.ts",
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile: path.resolve(
          here,
          "..",
          "..",
          "docs",
          "audits",
          "frontend-test-isolation-2026-08",
          "reversed-order-run.json",
        ),
      },
    ],
  ],
  use: {
    headless: true,
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
