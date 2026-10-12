import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC_DIR = path.resolve(process.cwd(), "src");
const TEST_SUPPORT_PREFIX = "test/";
const NON_POLL_INTERVAL_FILES = ["components/QueuePausedBanner.tsx"];
const POLLING_INTERVAL_FILES = ["api/ServerEventsProvider.tsx"];
const STALE_GUARD_NAMES = /mountedRef|generationRef|latestCallRef|refreshSeq|hasMountedRef/;
const STALE_GUARD_EXCEPTION_FILES = [
  "features/chat/hooks/useAutoDispatchRun.ts",
  "features/home/hooks/useConnectStates.ts",
  "features/home/hooks/useHomeData.ts",
];
const SETTER_LITERAL_CALL = /\b(set[A-Z]\w*)\((true|false)\)/g;
const BUSY_SETTER_NAMES = new Set([
  "setBusy",
  "setSaving",
  "setSubmitting",
  "setDispatching",
  "setDeleting",
  "setFinishing",
]);

interface SourceFile {
  relPath: string;
  text: string;
}

function isProductionSource(relPath: string): boolean {
  return (
    /\.tsx?$/.test(relPath) &&
    !/\.test\.[^/]+$/.test(relPath) &&
    !relPath.startsWith(TEST_SUPPORT_PREFIX)
  );
}

function collectProductionSources(dir: string): SourceFile[] {
  const files: SourceFile[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile()) continue;
    const absPath = path.join(entry.parentPath, entry.name);
    const relPath = path.relative(dir, absPath).split(path.sep).join("/");
    if (isProductionSource(relPath)) files.push({ relPath, text: readFileSync(absPath, "utf-8") });
  }
  return files;
}

function countLines(files: readonly SourceFile[], pattern: RegExp): number {
  return files.reduce(
    (total, file) => total + file.text.split("\n").filter((line) => pattern.test(line)).length,
    0,
  );
}

function countSetterTwins(files: readonly SourceFile[]): number {
  let twins = 0;
  for (const file of files.filter((f) => f.relPath.endsWith(".ts"))) {
    const literalsBySetter = new Map<string, Set<string>>();
    for (const [, setter, literal] of file.text.matchAll(SETTER_LITERAL_CALL)) {
      literalsBySetter.set(setter!, (literalsBySetter.get(setter!) ?? new Set()).add(literal!));
    }
    twins += [...literalsBySetter.values()].filter((literals) => literals.size === 2).length;
  }
  return twins;
}

const SOURCES = collectProductionSources(SRC_DIR);

describe("server-state measurement drift guard", () => {
  it("measures production frontend source only, never a test file or a test-support helper", () => {
    expect(SOURCES.length).toBeGreaterThan(0);
    expect(SOURCES.filter((f) => f.relPath.includes(".test."))).toEqual([]);
    expect(SOURCES.filter((f) => f.relPath.startsWith(TEST_SUPPORT_PREFIX))).toEqual([]);
  });

  it("keeps effect sites (lines containing useEffect with an opening parenthesis) at or below 58", () => {
    expect(countLines(SOURCES, /useEffect\(/)).toBeLessThanOrEqual(58);
  });

  it("keeps fetch-polling intervals (setInterval sites minus the named non-poll exclusions) at or below 1", () => {
    const polling = SOURCES.filter((f) => !NON_POLL_INTERVAL_FILES.includes(f.relPath));
    expect(countLines(polling, /setInterval\(/)).toBeLessThanOrEqual(1);
  });

  it("holds the one polling interval in the disconnected-SSE fallback and nowhere else", () => {
    const holders = SOURCES.filter(
      (f) => !NON_POLL_INTERVAL_FILES.includes(f.relPath) && f.text.includes("setInterval("),
    );
    expect(holders.map((f) => f.relPath)).toEqual(POLLING_INTERVAL_FILES);
  });

  it("keeps stale-response guard-flag declarations (let cancelled/ignore = false) at or below 3", () => {
    expect(countLines(SOURCES, /let (cancelled|ignore) = false/)).toBeLessThanOrEqual(3);
  });

  it("keeps busy-state setter twins (distinct file and setter pairs called with both true and false in non-component .ts modules) at or below 13", () => {
    expect(countSetterTwins(SOURCES)).toBeLessThanOrEqual(13);
  });

  it("confines the mounted/generation/sequence stale guards to the three named exceptions", () => {
    const holders = SOURCES.filter((f) => STALE_GUARD_NAMES.test(f.text)).map((f) => f.relPath);
    expect([...holders].sort()).toEqual(STALE_GUARD_EXCEPTION_FILES);
  });

  it("keeps the six shared-action-hook busy names out of production source outside src/ui/", () => {
    const outside = SOURCES.filter((f) => !f.relPath.startsWith("ui/"));
    const hits = outside.flatMap((f) =>
      [...f.text.matchAll(SETTER_LITERAL_CALL)]
        .filter(([, setter]) => BUSY_SETTER_NAMES.has(setter!))
        .map(([, setter, literal]) => `${f.relPath}:${setter!}(${literal!})`),
    );
    expect(hits).toEqual([]);
  });

  it("keeps the health context free of an effect and an interval", () => {
    const provider = SOURCES.find((f) => f.relPath === "api/HealthProvider.tsx");
    expect(provider).toBeDefined();
    expect(provider!.text).not.toContain("useEffect(");
    expect(provider!.text).not.toContain("setInterval(");
  });

  it("keeps the engine-resource adapter free of a generation counter, an effect and an interval", () => {
    const adapter = SOURCES.find((f) => f.relPath === "hooks/useEngineResource.ts");
    expect(adapter).toBeDefined();
    expect(adapter!.text).not.toContain("generationRef");
    expect(adapter!.text).not.toContain("useEffect(");
    expect(adapter!.text).not.toContain("setInterval(");
  });

  it("keeps the engine list free of local state, an effect and a mounted-ref stale guard", () => {
    const hook = SOURCES.find((f) => f.relPath === "hooks/useEngines.ts");
    expect(hook).toBeDefined();
    expect(hook!.text).not.toContain("useState");
    expect(hook!.text).not.toContain("useEffect");
    expect(hook!.text).not.toContain("mountedRef");
  });
});
