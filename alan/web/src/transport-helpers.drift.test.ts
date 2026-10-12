import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC_DIR = path.resolve(process.cwd(), "src");
const TRANSPORT_HELPERS = ["request", "fetchOk", "sanitizeApiUrl"] as const;
const PRESERVED_VALIDATION_GUARDS = ["assertSafeSegment", "assertSafeUrlPath"] as const;

const PINNED_CALLERS: Readonly<Record<string, readonly string[]>> = {
  sanitizeApiUrl: [
    "authedControlTokenMutation",
    "deleteSecret",
    "getControlTokenStatus",
    "proposeEngineConfig",
    "runSseSubscription",
    "setEngineDependencySweepEnabled",
  ],
};

const PRESERVED_GUARD_CALLERS_ON_THE_GENERATED_CLIENT = ["getEnginePresetConfig", "getWorkspace"];

const TOKEN_RE =
  /\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`/g;
const FUNCTION_START_RE = /^(?:export\s+)?(?:async\s+)?function\s+(\w+)/;

const CLASSIFICATION =
  "the helpers fall in three categories, not two: retirement candidates (request, fetchOk, " +
  "sanitizeApiUrl), preserved user-visible validation on the migrated path (assertSafeSegment, " +
  "assertSafeUrlPath), and everything else. The transport pin covers the first category only";

function stripCommentsAndStrings(source: string): string {
  return source.replace(TOKEN_RE, (token) => (token.startsWith("/") ? " " : '""'));
}

function isNonTestSourceFile(name: string): boolean {
  return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name);
}

function readSourceFiles(): { relPath: string; code: string }[] {
  const files: { relPath: string; code: string }[] = [];
  for (const entry of readdirSync(SRC_DIR, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !isNonTestSourceFile(entry.name)) continue;
    const absPath = path.join(entry.parentPath, entry.name);
    const relPath = path.relative(SRC_DIR, absPath).split(path.sep).join("/");
    if (relPath === "api/schema.ts" || relPath.startsWith("test/")) continue;
    files.push({ relPath, code: stripCommentsAndStrings(readFileSync(absPath, "utf-8")) });
  }
  return files;
}

function callersOf(code: string, helper: string): string[] {
  const call = new RegExp(`\\b${helper}\\s*[<(]`, "g");
  const callers: string[] = [];
  let enclosing = "<module>";
  for (const line of code.split("\n")) {
    const start = FUNCTION_START_RE.exec(line);
    if (start?.[1] !== undefined) enclosing = start[1];
    if (start?.[1] === helper) continue;
    callers.push(...Array.from(line.matchAll(call), () => enclosing));
  }
  return callers.sort();
}

function definesFunction(code: string, helper: string): boolean {
  return new RegExp(`(?:^|\\n)(?:export\\s+)?(?:async\\s+)?function\\s+${helper}\\b`).test(code);
}

const files = readSourceFiles();
const api = files.find((f) => f.relPath === "api.ts")?.code ?? "";

describe("transport helper drift guard", () => {
  it("reads a comment-and-string-free api.ts that still contains the exported api object", () => {
    expect(api).toContain("export const api = {");
  });

  it("pins a caller set for every helper still defined, and for none that was retired", () => {
    const defined = TRANSPORT_HELPERS.filter((helper) => definesFunction(api, helper));
    expect(
      Object.keys(PINNED_CALLERS).sort(),
      "PINNED_CALLERS must name exactly the transport helpers api.ts still defines. A helper " +
        "that was retired takes its pin with it; a helper added back needs a pin with a " +
        "recorded reason. Tightening-only: widening the pin to land a new caller is a gate " +
        "downgrade, not a fix. " +
        CLASSIFICATION,
    ).toEqual([...defined].sort());
    for (const callers of Object.values(PINNED_CALLERS)) expect(callers.length).toBeGreaterThan(0);
  });

  it.each(TRANSPORT_HELPERS)("%s is referenced only by its pinned callers", (helper) => {
    expect(
      callersOf(api, helper),
      `${helper} gained or lost a caller. A new call site is a second way to talk to the ` +
        "backend beside the generated client: move it onto the generated client, or if it " +
        "cannot move, record why and pin it here. Retiring the last caller means deleting the " +
        "helper and its pin. Tightening-only: widening the pin is a gate downgrade. " +
        CLASSIFICATION,
    ).toEqual([...(PINNED_CALLERS[helper] ?? [])]);
  });

  it("no module declares or calls a helper that api.ts has retired", () => {
    const retired = TRANSPORT_HELPERS.filter((helper) => !definesFunction(api, helper));
    const offenders = files.flatMap((f) =>
      retired
        .filter((helper) => definesFunction(f.code, helper) || callersOf(f.code, helper).length > 0)
        .map((helper) => `${f.relPath}: ${helper}`),
    );
    expect(
      offenders,
      "a retired transport helper was reintroduced. Reintroducing one anywhere under src/ " +
        "recreates the second transport the generated client replaced.",
    ).toEqual([]);
  });
});

describe("preserved validation is not a transport helper", () => {
  it.each(PRESERVED_VALIDATION_GUARDS)("%s is outside the transport pin", (guard) => {
    expect(
      Object.keys(PINNED_CALLERS),
      `${guard} is preserved user-visible validation, not a retirement candidate. ` + CLASSIFICATION,
    ).not.toContain(guard);
    expect([...TRANSPORT_HELPERS] as string[]).not.toContain(guard);
  });

  it("assertSafeUrlPath keeps callers on the generated client, so retiring sanitizeApiUrl cannot orphan it", () => {
    const callers = callersOf(api, "assertSafeUrlPath");
    expect(
      callers,
      "assertSafeUrlPath is called as standalone pre-flight validation by generated-client " +
        "wrappers, independently of sanitizeApiUrl, so it stays permanent when the last " +
        "sanitizeApiUrl caller goes. " +
        CLASSIFICATION,
    ).toEqual(expect.arrayContaining(PRESERVED_GUARD_CALLERS_ON_THE_GENERATED_CLIENT));
  });

  it("assertSafeSegment stays defined and called", () => {
    expect(definesFunction(api, "assertSafeSegment")).toBe(true);
    expect(callersOf(api, "assertSafeSegment").length).toBeGreaterThan(0);
  });
});
