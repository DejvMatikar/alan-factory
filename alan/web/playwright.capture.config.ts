import { defineConfig, devices, type PlaywrightTestConfig } from "@playwright/test";

import {
  resolveBuildDir,
  resolveCapturePort,
  resolveCaptureRoot,
  resolveScreensDir,
} from "./src/capture-config";
import { makeWebServer } from "./playwright.config";

// Up to ALAN_DISPATCHER_CONCURRENCY (default 6) autonomous workers can
// invoke `npm run capture:screens` concurrently from separate
// worktrees/slots, so a fixed port/root would bind-fail or race rm -rf.
// Planning-team workers additionally share ONE production checkout
// (see docs/architecture/screen_capture.md), so a fixed build output or screenshot directory
// would collide too — resolveBuildDir/resolveScreensDir (src/capture-config)
// derive those the same pid-cached way. playwright.mobile.config.ts now
// derives its own independent port/root the same way (src/mobile-audit-config.ts)
// rather than the fixed port/root this comment used to contrast against. See
// docs/architecture/screen_capture.md's "Shared-checkout concurrency"
// section.
const CAPTURE_PORT = resolveCapturePort();
const CAPTURE_ROOT = resolveCaptureRoot();
const BUILD_DIR = resolveBuildDir();
// Only used for its process.env caching side effect here — capture.spec.ts
// runs in a separate worker process and reads ALAN_CAPTURE_SCREENS_DIR back
// out of the environment directly.
resolveScreensDir();

type SingleWebServerConfig = Exclude<NonNullable<PlaywrightTestConfig["webServer"]>, unknown[]>;

const BASE_WEB_SERVER = makeWebServer(CAPTURE_PORT, CAPTURE_ROOT) as SingleWebServerConfig;

export default defineConfig({
  testDir: "./e2e/capture",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 60_000,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${CAPTURE_PORT}`,
    headless: true,
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 5"] },
    },
  ],
  webServer: {
    ...BASE_WEB_SERVER,
    // vite build only — no `tsc -b`, which would race the shared
    // tsconfig.tsbuildinfo incremental file across concurrent runs; standard
    // CI (`npm run typecheck`) already gates types on every PR regardless,
    // and a clean build failure here just degrades capture to
    // advisory-unavailable.
    command: `npx vite build --outDir "${BUILD_DIR}" --emptyOutDir && ${BASE_WEB_SERVER.command}`,
    timeout: 300_000,
    env: { ...BASE_WEB_SERVER.env, ALAN_WEB_DIST_DIR: BUILD_DIR },
  },
});
