import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const DOC_PATH = path.resolve(process.cwd(), "../../docs/DESIGN_SYSTEM.md");
const SRC_DIR = path.resolve(process.cwd(), "src");
const UI_DIR_PREFIX = "ui/";

const FROZEN_NATIVE_SUMMARY_FILES = [
  "features/chat/components/ProposalPanel.tsx",
  "features/engine/components/EngineConfigReadOnly.tsx",
  "features/project/pages/Project.tsx",
];

function isNonTestSourceFile(name: string): boolean {
  return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name);
}

function sourceFilesOutsideUi(dir: string): Map<string, string> {
  const files = new Map<string, string>();
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !isNonTestSourceFile(entry.name)) continue;
    const absPath = path.join(entry.parentPath, entry.name);
    const relPath = path.relative(dir, absPath).split(path.sep).join("/");
    if (relPath.startsWith(UI_DIR_PREFIX)) continue;
    files.set(relPath, readFileSync(absPath, "utf-8"));
  }
  return files;
}

function readFlatDoc(): string {
  return readFileSync(DOC_PATH, "utf-8").replace(/\s+/g, " ");
}

describe("disclosure drift guard", () => {
  it("keeps every native details-marker override inside src/ui/", () => {
    const offenders = [...sourceFilesOutsideUi(SRC_DIR)]
      .filter(([, text]) => text.includes("details-marker"))
      .map(([relPath]) => relPath);

    expect(offenders).toEqual([]);
  });

  it("keeps native <summary elements outside src/ui/ to exactly the frozen three, shrink-only", () => {
    const files = [...sourceFilesOutsideUi(SRC_DIR)]
      .filter(([, text]) => text.includes("<summary"))
      .map(([relPath]) => relPath)
      .sort();

    expect(files).toEqual(FROZEN_NATIVE_SUMMARY_FILES);
  });

  it("names src/ui/Disclosure.tsx in DESIGN_SYSTEM.md as the shared disclosure control", () => {
    expect(readFlatDoc()).toContain("src/ui/Disclosure.tsx");
  });

  it("records the default-open rule in DESIGN_SYSTEM.md", () => {
    const doc = readFlatDoc();

    expect(doc).toContain("primary content starts open");
    expect(doc).toContain("secondary detail starts closed");
  });

  it("records ▸/▾ as the required disclosure affordance in DESIGN_SYSTEM.md", () => {
    expect(readFlatDoc()).toContain("▸/▾");
  });

  it("names DescribedIconButton in DESIGN_SYSTEM.md as the control for glyph-only closers", () => {
    expect(readFlatDoc()).toContain("DescribedIconButton");
  });
});
