import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, it } from "vitest";

const CONFIG_PATH = path.resolve(process.cwd(), "playwright.capture.config.ts");
const SPEC_PATH = path.resolve(process.cwd(), "e2e/capture/capture.spec.ts");
const FORBIDDEN_TOKENS = ["compareToBaseline", "baseline.json", ".toBe(true)"];

it("capture config and spec contain no baseline gate", () => {
  const config = readFileSync(CONFIG_PATH, "utf-8");
  const spec = readFileSync(SPEC_PATH, "utf-8");

  for (const token of FORBIDDEN_TOKENS) {
    expect(config).not.toContain(token);
    expect(spec).not.toContain(token);
  }
});
