import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC_DIR = path.resolve(process.cwd(), "src");

function isScannedSourceFile(relPath: string): boolean {
  if (!/\.tsx?$/.test(relPath)) return false;
  if (/\.test\.tsx?$/.test(relPath)) return false;
  if (relPath.startsWith("test/")) return false;
  if (relPath.startsWith("ui/")) return false;
  return true;
}

function collectMatches(dir: string): string[] {
  const matches: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile()) continue;
    const absPath = path.join(entry.parentPath, entry.name);
    const relPath = path.relative(dir, absPath).split(path.sep).join("/");
    if (!isScannedSourceFile(relPath)) continue;
    const content = readFileSync(absPath, "utf-8");
    if (content.includes(".showModal(")) matches.push(relPath);
  }
  return matches;
}

describe("no native showModal outside src/ui/", () => {
  it("finds zero .showModal( calls in non-test production source outside src/ui/", () => {
    expect(collectMatches(SRC_DIR)).toEqual([]);
  });
});
