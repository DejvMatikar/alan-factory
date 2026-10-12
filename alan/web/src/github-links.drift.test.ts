import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC_DIR = path.resolve(process.cwd(), "src");
const GITHUB_LITERAL = "https://github.com/";
const ALLOWED_MODULE = "lib/githubLinks.ts";
const EXEMPT_FILES = ["features/connect/pages/ConnectStep1Identify.tsx", "components/CredentialsSection.tsx"];
const OWNER_SLUG_LITERAL = "DejvMatikar";
const TEST_SUPPORT_DIR = "test/";

function isNonTestSourceFile(name: string): boolean {
  return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name);
}

interface SourceFile {
  relPath: string;
  content: string;
}

function collectSourceFiles(dir: string): SourceFile[] {
  const files: SourceFile[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !isNonTestSourceFile(entry.name)) continue;
    const absPath = path.join(entry.parentPath, entry.name);
    const relPath = path.relative(dir, absPath).split(path.sep).join("/");
    files.push({ relPath, content: readFileSync(absPath, "utf-8") });
  }
  return files;
}

describe("github-links drift guard", () => {
  const files = collectSourceFiles(SRC_DIR);

  it("confines the https://github.com/ literal to githubLinks.ts plus the exempted placeholder files", () => {
    for (const { relPath, content } of files) {
      if (!content.includes(GITHUB_LITERAL)) continue;
      const allowed = relPath === ALLOWED_MODULE || EXEMPT_FILES.includes(relPath);
      expect(
        allowed,
        `unexpected https://github.com/ literal in ${relPath}. githubLinks.ts is the sole ` +
          "builder of pullUrl/commitUrl/blobUrl/blobBaseUrl, all of which take a " +
          "non-nullable RepoSlug — a call site with an unresolved repository must render " +
          "through components/GithubRef.tsx's non-clickable neutral-status branch, never " +
          "fall back to a guessed link built from a hardcoded default repo.",
      ).toBe(true);
    }
  });

  it("keeps the exemption set exactly the two placeholder files, so it can't quietly grow", () => {
    const filesWithLiteral = files
      .filter((f) => f.content.includes(GITHUB_LITERAL) && f.relPath !== ALLOWED_MODULE)
      .map((f) => f.relPath)
      .sort();
    expect(
      filesWithLiteral,
      "widening EXEMPT_FILES to land a new https://github.com/ literal is a gate " +
        "downgrade, not a fix — the only legal remedy is threading a real " +
        "repo: RepoSlug | null through to components/GithubRef.tsx instead.",
    ).toEqual([...EXEMPT_FILES].sort());
  });

  it("fails if a stale exemption entry no longer carries the literal", () => {
    for (const relPath of EXEMPT_FILES) {
      const entry = files.find((f) => f.relPath === relPath);
      expect(entry, `exempted file ${relPath} not found under src/`).toBeDefined();
      expect(entry?.content.includes(GITHUB_LITERAL)).toBe(true);
    }
  });

  it("keeps the owner's repository slug out of every non-test source file outside src/test/", () => {
    for (const { relPath, content } of files) {
      if (relPath.startsWith(TEST_SUPPORT_DIR)) continue;
      expect(
        content.includes(OWNER_SLUG_LITERAL),
        `${relPath} names the owner's repository (${OWNER_SLUG_LITERAL}). Production code must ` +
          "never fall back to a hardcoded default repository — an unresolved default is a null " +
          "key that renders no label, link or badge, not a guessed slug.",
      ).toBe(false);
    }
  });

  it("requires every exempted occurrence to sit on a placeholder= line, so it can't smuggle in real URL construction", () => {
    for (const relPath of EXEMPT_FILES) {
      const entry = files.find((f) => f.relPath === relPath);
      const lines = (entry?.content ?? "").split("\n");
      const literalLines = lines.filter((line) => line.includes(GITHUB_LITERAL));
      expect(literalLines.length).toBeGreaterThan(0);
      for (const line of literalLines) {
        expect(
          line,
          `line does not carry placeholder=", so it is real URL construction rather than ` +
            `an input placeholder, and the exemption does not cover it: ${line}`,
        ).toContain('placeholder="');
      }
    }
  });
});
