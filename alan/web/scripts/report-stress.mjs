/**
 * Emit a GitHub step-summary block reporting how many tests the nightly e2e
 * stress sweep (--repeat-each=3, --retries=0) actually executed, passed,
 * failed and skipped, plus wall-clock duration, from playwright-results.json.
 * Today a green nightly states nothing but "no NEW flaky spec files" — a
 * claim that is true by construction even when the suite ran a handful of
 * tests or zero. This script makes the executed count visible on every run
 * (default mode) and, in --enforce mode, fails the job when that count — or
 * the distinct spec-file count — drops below the checked-in floor in
 * e2e/stress-floor.json, so a broken filter, a discovery-path regression, or
 * a suite that quietly stops covering itself cannot stay green.
 *
 * Default mode always exits 0, including every fail-closed case below — the
 * workflow's summary step runs with `if: always()` so a report can
 * legitimately not exist (the stress step itself failed before writing one).
 * --enforce mode is a separate, later workflow step gated on
 * `steps.stress.outcome == 'success'`, and fails closed: a missing or
 * unparseable report, a report whose stats fields (expected/unexpected/
 * flaky/skipped/duration) aren't all finite numbers, or a missing or
 * malformed floor file, is treated the same as a floor breach — passing a
 * required-looking check having verified nothing is the same silent gap
 * report-flaky.mjs's --ratchet mode already closes on the flaky side. A
 * malformed stats shape is never silently coerced into a passing comparison
 * (e.g. a string field would otherwise concatenate instead of add, and a
 * missing field would otherwise NaN every comparison false) — default mode
 * still prints with invalid fields defaulted to 0, since it must never fail
 * the job, but --enforce mode refuses to compare against data it can't trust.
 *
 * "Executed" = stats.expected + stats.unexpected + stats.flaky (skipped
 * tests are runtime self-skips, not executions, and are deliberately
 * excluded so a test that skips itself cannot inflate the floor-guarded
 * number). Runtime skips are reported by name, not failed — a rising count
 * across nightlies is the erosion signal; making a single new runtime skip
 * fatal would red the very first nightly this ships against
 * (health-metrics-panel.spec.ts's seeded-data skip). Skip counts are printed
 * two ways because they legitimately differ: stats.skipped is the raw
 * Playwright count (--repeat-each=3 triples a self-skipping test into three
 * entries), while the named list below is deduped by `file › title` so one
 * self-skipping test reads as one problem, not three.
 *
 * Failed tests are named the same way, reusing the exact `- FAILED: file ›
 * title` row shape report-flaky.mjs already uses, and sourced from the same
 * shared playwright-results.mjs walk, so the two reports can never disagree
 * about what "failed" means. Deduped across the 3 repeats — one test failing
 * on all 3 repeats is one row, not three — a failing nightly's filed issue
 * now identifies every failed test by spec file and title, without anyone
 * needing to open the CI run.
 *
 * "Truncated" (unexpected >= maxFailures, from report.config.maxFailures
 * with a literal fallback of 5 matching playwright.config.ts's CI value)
 * means the run stopped early on maxFailures and the printed counts are a
 * floor, not the true total — NOT unexpected > 0, which would mislabel an
 * ordinary 1-4-failure run as truncated.
 *
 * --fingerprint prints a short, stable dedup marker for the run's full failed
 * set (never the truncated printed list, counts, or duration, all of which
 * drift nightly even when the same tests are failing) so the workflow can
 * skip re-commenting an already-open issue that names the same failures.
 * Every degraded case (missing/unreadable report, malformed JSON, zero named
 * failures) prints nothing and exits 0, so an empty marker falls back to the
 * file-finding-issue action's always-comment behaviour rather than
 * collapsing every undiagnosable night into one suppressed comment.
 */
import { createHash } from "node:crypto";
import fs from "node:fs";

import { collectOutcomes, readReport } from "./playwright-results.mjs";

const FLOOR_PATH = "e2e/stress-floor.json";
const DEFAULT_MAX_FAILURES = 5;

const args = process.argv.slice(2);
const enforce = args.includes("--enforce");
const fingerprint = args.includes("--fingerprint");
const reportPath = args.find((a) => !a.startsWith("--")) ?? "playwright-results.json";

function printSummary(body) {
  console.log(`### Playwright E2E stress sweep\n\n${body}`);
}

function formatDuration(ms) {
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s (${ms}ms)`;
}

if (fingerprint) {
  const read = readReport(reportPath);
  if (read.ok) {
    const { entries } = collectOutcomes(read.report);
    const failed = new Set();
    for (const { file, title, status } of entries) {
      if (status === "unexpected") failed.add(`${file} › ${title}`);
    }
    if (failed.size > 0) {
      const sorted = [...failed].sort();
      const digest = createHash("sha256").update(sorted.join("\n")).digest("hex").slice(0, 12);
      console.log(`stress-fail-${failed.size}-${digest}`);
    }
  }
  process.exit(0);
}

const read = readReport(reportPath);
if (!read.ok && read.reason === "missing") {
  printSummary("_no report file found_");
  if (enforce) {
    console.error(
      `Stress floor: ${reportPath} not found in ${process.cwd()} — the Playwright json reporter ` +
        `(playwright.config.ts) did not write it, but the E2E suite reported success. The floor check ` +
        `refuses to pass without data; this is NOT a flaky test.`,
    );
    process.exit(1);
  }
  process.exit(0);
}
if (!read.ok) {
  printSummary("_report file unreadable_");
  if (enforce) {
    console.error(
      `Stress floor: ${reportPath} in ${process.cwd()} could not be read or parsed as JSON (${read.error.message}) ` +
        `— the Playwright json reporter may have written a malformed file, or the file may simply be ` +
        `unreadable, but the E2E suite reported success. The floor check refuses to pass without data; this ` +
        `is NOT a flaky test.`,
    );
    process.exit(1);
  }
  process.exit(0);
}
const report = read.report;

function finiteOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const stats = report.stats ?? {};
const statFields = {
  expected: finiteOrNull(stats.expected),
  unexpected: finiteOrNull(stats.unexpected),
  flaky: finiteOrNull(stats.flaky),
  skipped: finiteOrNull(stats.skipped),
  duration: finiteOrNull(stats.duration),
};
const statsValid = Object.values(statFields).every((v) => v !== null);
const expected = statFields.expected ?? 0;
const unexpected = statFields.unexpected ?? 0;
const flaky = statFields.flaky ?? 0;
const skippedRaw = statFields.skipped ?? 0;
const durationMs = statFields.duration ?? 0;
const executed = expected + unexpected + flaky;

const { specFiles, entries } = collectOutcomes(report);
const skippedTests = new Set();
const failedTests = new Set();
for (const { file, title, status } of entries) {
  if (status === "skipped") {
    skippedTests.add(`${file} › ${title}`);
  } else if (status === "unexpected") {
    failedTests.add(`${file} › ${title}`);
  }
}

const maxFailures =
  typeof report.config?.maxFailures === "number" && report.config.maxFailures > 0
    ? report.config.maxFailures
    : DEFAULT_MAX_FAILURES;
const truncated = unexpected >= maxFailures;

const lines = [
  `- Executed: ${executed}`,
  `- Passed: ${expected}`,
  `- Failed: ${unexpected}`,
  `- Skipped: ${skippedRaw} (${skippedTests.size} distinct tests)`,
  `- Spec files: ${specFiles.size}`,
  `- Duration: ${formatDuration(durationMs)}`,
];
if (truncated) {
  lines.push("", "_counts truncated — run stopped early after hitting maxFailures_");
}
if (!statsValid) {
  lines.push(
    "",
    "_stats shape malformed — expected/unexpected/flaky/skipped/duration were not all finite numbers; " +
      "missing ones defaulted to 0 above_",
  );
}
if (failedTests.size > 0) {
  lines.push("", "Failed tests:", ...[...failedTests].sort().map((k) => `- FAILED: ${k}`));
}
if (skippedTests.size > 0) {
  lines.push("", "Skipped tests:", ...[...skippedTests].sort().map((k) => `- ${k}`));
}
printSummary(lines.join("\n"));

if (!enforce) {
  process.exit(0);
}

if (!statsValid) {
  console.error(
    `Stress floor: ${reportPath} in ${process.cwd()} has a malformed stats shape — expected, unexpected, ` +
      `flaky, skipped and duration must all be finite numbers, but at least one was missing, non-numeric, ` +
      `or non-finite. The floor check refuses to pass without valid data; this is NOT a flaky test.`,
  );
  process.exit(1);
}

if (!fs.existsSync(FLOOR_PATH)) {
  console.error(
    `Stress floor: ${FLOOR_PATH} not found in ${process.cwd()} — the floor check refuses to pass without ` +
      `a recorded floor to compare against.`,
  );
  process.exit(1);
}

let floor;
try {
  const parsed = JSON.parse(fs.readFileSync(FLOOR_PATH, "utf8"));
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("not a JSON object");
  }
  if (typeof parsed.minExecutedTests !== "number" || typeof parsed.minSpecFiles !== "number") {
    throw new Error("minExecutedTests and minSpecFiles must both be numbers");
  }
  floor = parsed;
} catch (err) {
  console.error(
    `Stress floor: ${FLOOR_PATH} in ${process.cwd()} is malformed (${err.message}) — expected a JSON ` +
      `object with numeric minExecutedTests and minSpecFiles.`,
  );
  process.exit(1);
}

if (executed < floor.minExecutedTests) {
  console.error(
    `Stress floor: executed ${executed} < floor ${floor.minExecutedTests} (see ${FLOOR_PATH}). The sweep ` +
      `ran fewer tests than expected — a broken filter, a discovery-path regression, or a suite that ` +
      `quietly stopped covering itself. If this drop is deliberate, lower minExecutedTests in ${FLOOR_PATH} ` +
      `by hand.`,
  );
  process.exit(1);
}

if (specFiles.size < floor.minSpecFiles) {
  console.error(
    `Stress floor: spec files executed ${specFiles.size} < floor ${floor.minSpecFiles} (see ${FLOOR_PATH}). ` +
      `Fewer spec files ran than expected — a broken filter or discovery-path regression. If this drop is ` +
      `deliberate, lower minSpecFiles in ${FLOOR_PATH} by hand.`,
  );
  process.exit(1);
}

console.log(
  `Stress floor: executed ${executed} >= ${floor.minExecutedTests}, spec files ${specFiles.size} >= ` +
    `${floor.minSpecFiles}.`,
);
process.exit(0);
