import { defineConfig, devices, type PlaywrightTestConfig } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { systemPortOps } from "./e2e/fixtures/port-ops";
import { reserveMobileAuditPort, resolveMobileAuditRoot } from "./src/mobile-audit-config";
import { makeWebServer } from "./playwright.config";

const here = path.dirname(fileURLToPath(import.meta.url));

// playwright.config.ts is loaded by Node's native ESM loader, which requires a JSON
// import attribute that src/ui-spec.ts's bundler-style `import ... from "./ui-spec.json"`
// does not use. Read the JSON source of truth directly instead of importing ui-spec.ts.
const uiSpec = JSON.parse(
  fs.readFileSync(path.join(here, "src", "ui-spec.json"), "utf-8"),
) as { viewport: { floorWidthPx: number } };

// Pid-derived, not a fixed literal — two `audit:mobile` invocations from the
// same checkout (planning-team workers share one production checkout, see
// docs/architecture/screen_capture.md) must never bind-conflict or
// `rm -rf` each other's live state root mid-run. mobile-audit.spec.ts reads
// the root through resolveMobileAuditRoot() itself (src/mobile-audit-config.ts)
// rather than importing it from here, since importing this config file would
// re-run makeWebServer's setup at module-load time in every spec worker.
const MOBILE_AUDIT_PORT = reserveMobileAuditPort(systemPortOps);
const MOBILE_AUDIT_ROOT = resolveMobileAuditRoot();
const MOBILE_AUDIT_VIEWPORT_HEIGHT_PX = 844;

type SingleWebServerConfig = Exclude<NonNullable<PlaywrightTestConfig["webServer"]>, unknown[]>;

// reserveMobileAuditPort() above already frees or avoids this port at
// config-load time (src/port-avoidance.ts), so no webServer.command guard is
// needed here. The old inline guard this replaced was unreachable in exactly
// the case it was written for: Playwright pre-flights webServer.url before
// spawning command when reuseExistingServer is false, so a foreign listener
// already failed the bind before the guard's first line ever ran.
const BASE_WEB_SERVER = makeWebServer(
  MOBILE_AUDIT_PORT,
  MOBILE_AUDIT_ROOT,
) as SingleWebServerConfig;

export default defineConfig({
  testDir: "./e2e/mobile-audit",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 2,
  workers: 1,
  // One test loops all six screens, each with a settle poll + axe scan +
  // full-page screenshot, which can exceed Playwright's 30s default.
  timeout: 180_000,
  reporter: "list",
  globalTeardown: "./e2e/mobile-audit/global-teardown.ts",
  use: {
    baseURL: `http://127.0.0.1:${MOBILE_AUDIT_PORT}`,
    headless: true,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "mobile-chromium",
      use: {
        ...devices["Pixel 5"],
        viewport: {
          width: uiSpec.viewport.floorWidthPx,
          height: MOBILE_AUDIT_VIEWPORT_HEIGHT_PX,
        },
      },
    },
  ],
  webServer: {
    ...BASE_WEB_SERVER,
    // Seeds a populated health state + long queue-pause at startup so the
    // audit can scan the health page's badges/chips instead of only the
    // empty "all healthy" state (alan/reconciler.py). Baseline e2e specs
    // never set this — see server-env.ts's blank default.
    env: { ...BASE_WEB_SERVER.env, ALAN_E2E_HEALTH_FIXTURE: "1" },
  },
});
