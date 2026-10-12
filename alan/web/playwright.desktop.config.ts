import { defineConfig, devices, type PlaywrightTestConfig } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { systemPortOps } from "./e2e/fixtures/port-ops";
import {
  reserveDesktopAuditMaxPort,
  reserveDesktopAuditStandardPort,
  resolveDesktopAuditMaxRoot,
  resolveDesktopAuditStandardRoot,
} from "./src/desktop-audit-config";
import { makeWebServer } from "./playwright.config";

const here = path.dirname(fileURLToPath(import.meta.url));

// Node's native ESM loader (this config is loaded outside Vite/tsc's
// bundler-style resolution) requires a JSON import attribute src/ui-spec.ts's
// `import ... from "./ui-spec.json"` does not use — read the JSON source of
// truth directly, same workaround as playwright.mobile.config.ts.
const uiSpec = JSON.parse(fs.readFileSync(path.join(here, "src", "ui-spec.json"), "utf-8")) as {
  desktopAudit: { viewport: { widthPx: number; heightPx: number } };
};
const { widthPx: DESKTOP_VIEWPORT_WIDTH_PX, heightPx: DESKTOP_VIEWPORT_HEIGHT_PX } = uiSpec.desktopAudit.viewport;

const STANDARD_PORT = reserveDesktopAuditStandardPort(systemPortOps);
const STANDARD_ROOT = resolveDesktopAuditStandardRoot();
const MAX_PORT = reserveDesktopAuditMaxPort(systemPortOps);
const MAX_ROOT = resolveDesktopAuditMaxRoot();

type SingleWebServerConfig = Exclude<NonNullable<PlaywrightTestConfig["webServer"]>, unknown[]>;

const STANDARD_WEB_SERVER = makeWebServer(STANDARD_PORT, STANDARD_ROOT) as SingleWebServerConfig;
const MAX_WEB_SERVER = makeWebServer(MAX_PORT, MAX_ROOT) as SingleWebServerConfig;

export default defineConfig({
  testDir: "./e2e/desktop-audit",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 2,
  workers: 1,
  // Two states x thirteen screens, each with a settle poll + full DOM
  // geometry walk + full-page screenshot, can exceed Playwright's 30s
  // default comfortably within one test.
  timeout: 180_000,
  reporter: "list",
  globalTeardown: "./e2e/desktop-audit/global-teardown.ts",
  use: {
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop-standard",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: `http://127.0.0.1:${STANDARD_PORT}`,
        viewport: { width: DESKTOP_VIEWPORT_WIDTH_PX, height: DESKTOP_VIEWPORT_HEIGHT_PX },
      },
    },
    {
      name: "desktop-max",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: `http://127.0.0.1:${MAX_PORT}`,
        viewport: { width: DESKTOP_VIEWPORT_WIDTH_PX, height: DESKTOP_VIEWPORT_HEIGHT_PX },
      },
    },
  ],
  webServer: [
    {
      ...STANDARD_WEB_SERVER,
      // Matches today's mobile-audit seeding exactly — the standard desktop
      // state is "the state already seeded today" per the roadmap goal.
      env: { ...STANDARD_WEB_SERVER.env, ALAN_E2E_HEALTH_FIXTURE: "1" },
    },
    {
      ...MAX_WEB_SERVER,
      // Never both fixtures at once (alan/e2e_health_fixture.py raises if
      // both env vars are "1") — the max server carries only its own.
      env: { ...MAX_WEB_SERVER.env, ALAN_E2E_DESKTOP_MAX_HEALTH_FIXTURE: "1" },
    },
  ],
});
