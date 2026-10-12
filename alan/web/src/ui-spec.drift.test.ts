import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { tapTargetMinPx } from "./ui-spec";

const CSS_PATH = path.resolve(process.cwd(), "src/index.css");
const TAP_TARGET_TOKEN_PATTERN = /--spacing-tap-target:\s*([\d.]+)px/;

describe("ui-spec drift guard", () => {
  it("keeps the --spacing-tap-target CSS token equal to tapTargetMinPx", () => {
    const css = readFileSync(CSS_PATH, "utf-8");
    const match = TAP_TARGET_TOKEN_PATTERN.exec(css);

    expect(match).not.toBeNull();
    const cssTapTargetPx = Number(match?.[1]);
    expect(cssTapTargetPx).toBe(tapTargetMinPx);
  });
});
