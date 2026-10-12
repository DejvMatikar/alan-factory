/**
 * Emit a GitHub step-summary block listing flaky (passed-on-retry) and failed
 * Playwright tests from playwright-results.json. Flakes recovered by
 * retries:1 are invisible in a green run without this; the step summary turns
 * them into a per-run trend line. The default mode exits 0 always — advisory,
 * never blocking, even when the report is missing or unparseable. With
 * --ratchet it instead compares the run's flaky spec FILES against the
 * checked-in allowlist e2e/flaky-baseline.json and exits 1 when a file
 * outside the baseline flaked, so a PR that introduces a new flake class
 * fails in that PR instead of silently degrading main for weeks (the #903
 * regression shape). The workflow runs the ratchet on pull_request only and
 * success()-gated — a red run already has a first-order failure to report,
 * and push-to-main must never be blocked by a residual flake. In --ratchet
 * mode specifically, a missing or unparseable report also exits 1: the
 * ratchet is a required check, and passing it having checked nothing is a
 * silent gap the same as a missing baseline already closes on the other
 * side. A ratchet failure also emits one `::error file=…` workflow command
 * per fresh flaky spec file (on stderr — stdout is the step summary), so the
 * verdict lands on the check run as a file-anchored annotation that the
 * fleet's merge-gate classifier can join against the PR's own diff; the
 * human-readable stderr line alone carries no spec identity a machine can
 * attribute.
 *
 * Each flaky/failed entry also carries an optional machine-load reading —
 * os.loadavg()[0] normalised by os.availableParallelism(), recorded by an
 * e2e/fixtures/test-base.ts auto fixture at the moment a test's status first
 * diverges from its expected status. The reading is read from the FAILING
 * attempt's own per-result record (playwright-results.mjs's collectOutcomes
 * descends into results[] for this), never from the test's summary-level
 * annotations: a passed-on-retry test's summary reflects only its final,
 * passing attempt, which carries no reading at all — reading there would
 * silently return nothing on exactly the case this measurement exists to
 * catch. Only the first-attempt-failed / second-attempt-passed shape is ever
 * visible here, because a run that fails both attempts reds the suite step
 * before the ratchet executes at all.
 *
 * Deliberately, no threshold is defined anywhere on this reading. A bare
 * cut-off in a script has nothing ratcheting it, unlike every other baseline
 * in this repository (flaky-baseline.json, size_baseline.py, the mobile/
 * desktop audit high-water pins) — introducing one here would reintroduce
 * exactly the laundering surface this measurement-not-verdict design exists
 * to avoid. The reading is reported so a human (or a future, separately
 * built calibration pass) can judge contention; this script never does.
 */
import fs from "node:fs";
import path from "node:path";

import { collectOutcomes, readReport } from "./playwright-results.mjs";

const RATCHET_TITLE = "Flake ratchet: new flaky spec file";
const LOAD_TITLE = "E2E machine load at failure";

function escapeData(value) {
  return String(value).replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A");
}

function escapeProperty(value) {
  return escapeData(value).replace(/:/g, "%3A").replace(/,/g, "%2C");
}

function annotationPath(file) {
  const workspace = process.env.GITHUB_WORKSPACE;
  const relative = workspace
    ? path.relative(workspace, path.resolve("e2e", file))
    : path.join("e2e", file);
  return relative.split(path.sep).join("/");
}

function ratchetAnnotation(file) {
  const message =
    `${file} flaked (passed on retry) and is not in e2e/flaky-baseline.json — ` +
    "fix the flake; the baseline is a reviewed last resort.";
  return `::error file=${escapeProperty(annotationPath(file))},title=${escapeProperty(RATCHET_TITLE)}::${escapeData(message)}`;
}

function formatLoad(load) {
  return ` — load ${load.perCpu.toFixed(2)}/cpu (loadavg1 ${load.loadavg1.toFixed(2)}, cpus ${load.cpus})`;
}

function fileLoadAggregate(entries, file) {
  let best = null;
  let count = 0;
  for (const entry of entries) {
    if (entry.file !== file || entry.status !== "flaky" || !entry.load) continue;
    count += 1;
    if (!best || entry.load.perCpu > best.perCpu) best = entry.load;
  }
  return best ? { ...best, count } : null;
}

function loadAnnotation(file, aggregate) {
  const message =
    `${file} flaked at a measured machine load of ${aggregate.perCpu.toFixed(2)}/cpu ` +
    `(loadavg1 ${aggregate.loadavg1.toFixed(2)}, cpus ${aggregate.cpus}) across ${aggregate.count} ` +
    "failing attempt(s). This is an observation, not a verdict — no threshold is defined.";
  return `::notice file=${escapeProperty(annotationPath(file))},title=${escapeProperty(LOAD_TITLE)}::${escapeData(message)}`;
}

const args = process.argv.slice(2);
const ratchet = args.includes("--ratchet");
const reportPath = args.find((a) => !a.startsWith("--")) ?? "playwright-results.json";

const read = readReport(reportPath);
if (!read.ok && read.reason === "missing") {
  console.log("### Playwright E2E flake report\n\n_no report file found_");
  if (ratchet) {
    console.error(
      `Flake ratchet: ${reportPath} not found in ${process.cwd()} — the Playwright json reporter ` +
        `(playwright.config.ts) did not write it, but the E2E suite reported success. The ratchet ` +
        `refuses to pass without data; this is NOT a flaky test.`,
    );
    process.exit(1);
  }
  process.exit(0);
}
if (!read.ok) {
  console.log("### Playwright E2E flake report\n\n_report file unreadable_");
  if (ratchet) {
    console.error(
      `Flake ratchet: ${reportPath} in ${process.cwd()} could not be read or parsed as JSON (${read.error.message}) ` +
        `— the Playwright json reporter may have written a malformed file, or the file may simply be ` +
        `unreadable, but the E2E suite reported success. The ratchet refuses to pass without data; this ` +
        `is NOT a flaky test.`,
    );
    process.exit(1);
  }
  process.exit(0);
}

const rows = [];
const flakyFiles = new Set();
const { entries } = collectOutcomes(read.report);
for (const { file, title, status, load } of entries) {
  if (status === "flaky") {
    rows.push(`- FLAKY (passed on retry): ${file} › ${title}${load ? formatLoad(load) : ""}`);
    flakyFiles.add(file);
  } else if (status === "unexpected") {
    rows.push(`- FAILED: ${file} › ${title}`);
  }
}

if (ratchet) {
  let baseline;
  try {
    const parsed = JSON.parse(fs.readFileSync("e2e/flaky-baseline.json", "utf8"));
    if (!Array.isArray(parsed)) throw new Error("not an array");
    baseline = new Set(parsed);
  } catch {
    console.log("### Playwright E2E flake ratchet: e2e/flaky-baseline.json missing or invalid");
    console.error("Flake ratchet: e2e/flaky-baseline.json is missing or not a JSON array.");
    process.exit(1);
  }
  const fresh = [...flakyFiles].filter((f) => !baseline.has(f)).sort();
  if (fresh.length > 0) {
    const aggregates = new Map(fresh.map((f) => [f, fileLoadAggregate(entries, f)]));
    console.log(
      `### Playwright E2E flake ratchet: new flaky spec file(s)\n\n${fresh
        .map((f) => {
          const aggregate = aggregates.get(f);
          return `- ${f}${aggregate ? `${formatLoad(aggregate)} across ${aggregate.count} failing attempt(s)` : ""}`;
        })
        .join("\n")}\n\nFix the flake, or as a last resort add the file to e2e/flaky-baseline.json.`,
    );
    // The step redirects stdout into $GITHUB_STEP_SUMMARY, so the job log
    // would otherwise show a bare exit 1 with no explanation; the runner
    // scans stderr for workflow commands too, which is where the
    // file-anchored annotations must go.
    for (const f of fresh) {
      console.error(ratchetAnnotation(f));
      const aggregate = aggregates.get(f);
      if (aggregate) console.error(loadAnnotation(f, aggregate));
    }
    console.error(`Flake ratchet: new flaky spec file(s): ${fresh.join(", ")} — details in the step summary.`);
    process.exit(1);
  }
  console.log("### Playwright E2E flake ratchet: no new flaky spec files");
  process.exit(0);
}

console.log(
  rows.length > 0
    ? `### Playwright E2E flake report\n\n${rows.join("\n")}`
    : "### Playwright E2E: 0 flaky tests",
);
