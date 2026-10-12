import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertBaselineNotRaised,
  type DesktopAuditBaseline,
} from "../../src/desktop-audit/baseline-compare";
import { DESKTOP_AUDIT_BASELINE_HIGH_WATER } from "./baseline-high-water";

const here = path.dirname(fileURLToPath(import.meta.url));

export const DESKTOP_AUDIT_BASELINE_PATH = path.resolve(here, "baseline.json");

export const BASELINE_RAISE_REMEDIATION =
  "A legitimate baseline raise is a human call: hand-edit both " +
  "alan/web/e2e/desktop-audit/baseline.json and its pin at " +
  "alan/web/e2e/desktop-audit/baseline-high-water.ts in the same PR. There is no " +
  "regeneration mechanism for either file.";

export function loadRatchetedDesktopBaseline(
  baselinePath: string = DESKTOP_AUDIT_BASELINE_PATH,
): DesktopAuditBaseline {
  const committed = JSON.parse(fs.readFileSync(baselinePath, "utf-8")) as DesktopAuditBaseline;
  try {
    assertBaselineNotRaised(DESKTOP_AUDIT_BASELINE_HIGH_WATER, committed);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${message}\n${BASELINE_RAISE_REMEDIATION}`);
  }
  return committed;
}
