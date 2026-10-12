import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertBaselineNotRaised,
  type MobileAuditBaseline,
} from "../../src/mobile-audit/baseline-compare";
import { MOBILE_AUDIT_BASELINE_HIGH_WATER } from "./baseline-high-water";

const here = path.dirname(fileURLToPath(import.meta.url));

export const MOBILE_AUDIT_BASELINE_PATH = path.resolve(here, "baseline.json");

export const BASELINE_RAISE_REMEDIATION =
  "A legitimate baseline raise is a human call: hand-edit both " +
  "alan/web/e2e/mobile-audit/baseline.json and its pin at " +
  "alan/web/e2e/mobile-audit/baseline-high-water.ts in the same PR. There is no " +
  "regeneration mechanism for either file.";

export function loadRatchetedBaseline(
  baselinePath: string = MOBILE_AUDIT_BASELINE_PATH,
): MobileAuditBaseline {
  const committed = JSON.parse(fs.readFileSync(baselinePath, "utf-8")) as MobileAuditBaseline;
  try {
    assertBaselineNotRaised(MOBILE_AUDIT_BASELINE_HIGH_WATER, committed);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${message}\n${BASELINE_RAISE_REMEDIATION}`);
  }
  return committed;
}
