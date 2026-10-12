import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const DOC_PATH = path.resolve(process.cwd(), "../../docs/DESIGN_SYSTEM.md");
const SRC_DIR = path.resolve(process.cwd(), "src");
const UI_DIR_PREFIX = `ui${path.sep}`;
const CORE_ERROR_BANNER =
  "rounded-md bg-banner-error-tint/15 px-3 py-2 text-xs text-banner-error-fg";

function isNonTestSourceFile(name: string): boolean {
  return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name);
}

function collectSourceTextOutsideUi(dir: string): string {
  let combined = "";
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !isNonTestSourceFile(entry.name)) continue;
    const absPath = path.join(entry.parentPath, entry.name);
    const relPath = path.relative(dir, absPath);
    if (relPath.startsWith(UI_DIR_PREFIX)) continue;
    combined += readFileSync(absPath, "utf-8");
  }
  return combined;
}

describe("error-banner chrome drift guard", () => {
  it("keeps the core error-banner class string to no occurrences outside src/ui/", () => {
    const source = collectSourceTextOutsideUi(SRC_DIR);
    const occurrences = source.split(CORE_ERROR_BANNER).length - 1;

    expect(occurrences).toBeLessThanOrEqual(0);
  });

  it("names src/ui/ErrorBanner.tsx in DESIGN_SYSTEM.md's shared-primitives inventory", () => {
    const doc = readFileSync(DOC_PATH, "utf-8");

    expect(doc).toContain("src/ui/ErrorBanner.tsx");
  });
});
