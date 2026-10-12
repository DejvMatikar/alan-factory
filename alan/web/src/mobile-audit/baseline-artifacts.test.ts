import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  loadRatchetedBaseline,
  MOBILE_AUDIT_BASELINE_PATH,
} from "../../e2e/mobile-audit/load-baseline";
import type { MobileAuditBaseline, MobileAuditDimension } from "./baseline-compare";

const SPEC_PATH = path.resolve(process.cwd(), "e2e/mobile-audit/mobile-audit.spec.ts");
const FORBIDDEN_RAW_READ_PATTERN = /['"]baseline\.json['"]/;
const EMPTY_COUNTS = { overflow: 0, tapTarget: 0, contrast: 0 };

function realBaseline(): MobileAuditBaseline {
  return JSON.parse(readFileSync(MOBILE_AUDIT_BASELINE_PATH, "utf-8")) as MobileAuditBaseline;
}

function withCount(
  baseline: MobileAuditBaseline,
  screen: string,
  dimension: MobileAuditDimension,
  value: number,
): MobileAuditBaseline {
  const existing = baseline[screen] ?? EMPTY_COUNTS;
  return { ...baseline, [screen]: { ...existing, [dimension]: value } };
}

function writeTempBaseline(baseline: MobileAuditBaseline): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), "mobile-audit-baseline-"));
  const file = path.join(dir, "baseline.json");
  writeFileSync(file, JSON.stringify(baseline));
  return file;
}

function throwsFrom(baselinePath: string): Error {
  try {
    loadRatchetedBaseline(baselinePath);
  } catch (error) {
    return error as Error;
  }
  throw new Error("expected loadRatchetedBaseline to throw");
}

describe("loadRatchetedBaseline", () => {
  it("loads the committed baseline unchanged when it is not raised above the pin", () => {
    expect(loadRatchetedBaseline()).toEqual(realBaseline());
  });

  it("throws naming the screen, dimension, and pin file when a dimension count is raised", () => {
    const baseline = realBaseline();
    const originalValue = baseline["repo-engine-config"]!.tapTarget;
    const raisedValue = originalValue + 1000;
    const raised = withCount(baseline, "repo-engine-config", "tapTarget", raisedValue);
    const tempPath = writeTempBaseline(raised);
    try {
      const error = throwsFrom(tempPath);
      expect(error.message).toMatch(
        new RegExp(`repo-engine-config.*tapTarget.*raised from \\d+ to ${raisedValue}`),
      );
      expect(error.message).toContain("baseline-high-water.ts");
    } finally {
      rmSync(path.dirname(tempPath), { recursive: true, force: true });
    }
  });

  it("does not throw when a dimension count is lowered", () => {
    const baseline = realBaseline();
    const lowered = withCount(baseline, "home", "tapTarget", baseline.home!.tapTarget - 1);
    const tempPath = writeTempBaseline(lowered);
    try {
      expect(() => loadRatchetedBaseline(tempPath)).not.toThrow();
    } finally {
      rmSync(path.dirname(tempPath), { recursive: true, force: true });
    }
  });

  it("throws when an unpinned screen entry carries a nonzero count", () => {
    const withNewScreen = withCount(realBaseline(), "unpinned-screen", "overflow", 1);
    const tempPath = writeTempBaseline(withNewScreen);
    try {
      expect(() => loadRatchetedBaseline(tempPath)).toThrow(
        /unpinned-screen.*overflow.*raised from 0 to 1/,
      );
    } finally {
      rmSync(path.dirname(tempPath), { recursive: true, force: true });
    }
  });

  it("mobile-audit.spec.ts must call loadRatchetedBaseline() instead of reading baseline.json directly, since a future refactor to a real import() would break its module-scope resolveMobileAuditRoot() call under vitest", () => {
    const spec = readFileSync(SPEC_PATH, "utf-8");
    expect(spec).toContain("loadRatchetedBaseline");
    expect(spec).not.toMatch(FORBIDDEN_RAW_READ_PATTERN);
  });
});
