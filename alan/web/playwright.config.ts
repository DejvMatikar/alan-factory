import { defineConfig, devices, type PlaywrightTestConfig } from "@playwright/test";

import { TEST_DEFAULT_PROFILE } from "./e2e/fixtures/default-profile";
import { E2E_WORKERS, repoRoot, serverEnv, stubDir } from "./e2e/fixtures/server-env";

export function makeWebServer(
  port: number,
  root: string,
): NonNullable<PlaywrightTestConfig["webServer"]> {
  return {
    command: `bash -lc 'rm -rf "$E2E_ROOT_DIR" && mkdir -p "$E2E_ROOT_DIR/workspaces" && mkdir -p "$ALAN_ENGINES_ROOT" && cp "${TEST_DEFAULT_PROFILE}" "$ALAN_ENGINES_ROOT/default_profile.yaml" && export PATH="${stubDir}:$PATH" && cd "${repoRoot}" && uv run python -m alan --port ${port} --host 127.0.0.1'`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
    env: {
      E2E_ROOT_DIR: root,
      ...serverEnv(root, "e2e-stub-pr-branch"),
    },
  };
}

export default defineConfig({
  testDir: "./e2e",
  testIgnore: ["mobile-audit/**", "desktop-audit/**", "capture/**"],
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: E2E_WORKERS,
  // A hard-broken suite (e.g. server boot failure) should exit early with
  // diagnostics instead of burning every test × (timeout + retry) into the CI
  // job cap, which would kill the run before the failure-report upload.
  maxFailures: process.env.CI ? 5 : 0,
  // Every spec's internal expect.poll/expect budget must stay ≤ (test budget
  // − 15s); genuinely long flows override via test.setTimeout instead.
  timeout: 60_000,
  expect: { timeout: 10_000 },
  globalTeardown: "./e2e/global-teardown.ts",
  // The JSON report feeds scripts/report-flaky.mjs: a passed-on-retry test is
  // a flake that today hides inside a green run — the CI step summary makes
  // that signal visible on every run.
  reporter: process.env.CI
    ? [
        ["github"],
        ["html", { open: "never" }],
        ["json", { outputFile: "playwright-results.json" }],
      ]
    : "list",
  use: {
    // No baseURL here: each worker's server provides it via the e2eServer
    // fixture in e2e/fixtures/test-base.ts. A spec importing raw
    // @playwright/test fails loudly on relative navigation instead of
    // silently hitting another worker's server.
    headless: true,
    screenshot: "only-on-failure",
    // Video is the dominant per-test overhead when recorded for every test
    // (discarded on pass). In CI, record only the retry attempt — every CI
    // failure still yields full video+trace via retries: 1.
    video: process.env.CI ? "on-first-retry" : "off",
    trace: process.env.CI ? "on-first-retry" : "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
