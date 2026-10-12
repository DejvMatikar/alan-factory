import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const E2E_DIR = path.resolve(process.cwd(), "e2e");
const HIT_TEST_PATTERN = /elements?FromPoint/;
const REVEAL_GATE_LITERAL = "not.toHaveClass(/sr-only/";
const ALLOWED_MODULE = "fixtures/layering.ts";
const EXEMPT_FILES: string[] = [];

function isE2eSourceFile(name: string): boolean {
  return name.endsWith(".ts");
}

interface E2eFile {
  relPath: string;
  content: string;
}

function collectE2eFiles(dir: string): E2eFile[] {
  const files: E2eFile[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !isE2eSourceFile(entry.name)) continue;
    const absPath = path.join(entry.parentPath, entry.name);
    const relPath = path.relative(dir, absPath).split(path.sep).join("/");
    files.push({ relPath, content: readFileSync(absPath, "utf-8") });
  }
  return files;
}

describe("e2e hit-test confinement guard", () => {
  const files = collectE2eFiles(E2E_DIR);

  it("confines document.elementFromPoint/elementsFromPoint to the allowed helper plus exemptions", () => {
    for (const { relPath, content } of files) {
      if (!HIT_TEST_PATTERN.test(content)) continue;
      const allowed = relPath === ALLOWED_MODULE || EXEMPT_FILES.includes(relPath);
      expect(allowed, `unexpected hit-test call in ${relPath}`).toBe(true);
    }
  });

  it("keeps the exemption set empty, so it can't quietly grow", () => {
    const filesWithHitTest = files
      .filter((f) => HIT_TEST_PATTERN.test(f.content) && f.relPath !== ALLOWED_MODULE)
      .map((f) => f.relPath)
      .sort();
    expect(filesWithHitTest).toEqual([...EXEMPT_FILES].sort());
  });

  it("fails if a stale exemption entry no longer carries the pattern", () => {
    for (const relPath of EXEMPT_FILES) {
      const entry = files.find((f) => f.relPath === relPath);
      expect(entry, `exempted file ${relPath} not found under e2e/`).toBeDefined();
      expect(HIT_TEST_PATTERN.test(entry?.content ?? "")).toBe(true);
    }
  });

  it("keeps the allowed helper carrying both the hit-test call and its own reveal gate", () => {
    const helper = files.find((f) => f.relPath === ALLOWED_MODULE);
    expect(helper, `${ALLOWED_MODULE} not found under e2e/`).toBeDefined();
    const content = helper?.content ?? "";
    expect(HIT_TEST_PATTERN.test(content)).toBe(true);
    expect(content.includes(REVEAL_GATE_LITERAL)).toBe(true);
  });
});
