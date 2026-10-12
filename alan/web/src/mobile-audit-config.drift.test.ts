import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, it } from "vitest";

const CONFIG_PATH = path.resolve(process.cwd(), "playwright.mobile.config.ts");

it("mobile-audit config has no hardcoded port literal or homedir-rooted state root", () => {
  const config = readFileSync(CONFIG_PATH, "utf-8");
  expect(config).not.toContain("4174");
  expect(config).not.toContain("os.homedir()");
});

it("mobile-audit port reservation avoids a stale port owner at config-load time instead of a webServer.command guard", () => {
  const config = readFileSync(CONFIG_PATH, "utf-8");
  expect(config).toContain("reserveMobileAuditPort(systemPortOps)");
  expect(config).not.toContain("lsof");
});
