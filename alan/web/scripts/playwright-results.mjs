/**
 * Shared reader/walker for playwright-results.json (the Playwright json
 * reporter's output), used by report-flaky.mjs and report-stress.mjs so both
 * scripts agree on what counts as a failed, flaky or skipped test instead of
 * each maintaining its own copy of the same suites-to-specs-to-tests walk.
 *
 * Side-effect-free by design: no process.exit, no console output, no
 * top-level I/O. Callers own all exit codes and printing so this module can
 * be reused by scripts with different failure-handling policies (advisory
 * vs. fail-closed).
 */
import fs from "node:fs";

export function readReport(reportPath) {
  if (!fs.existsSync(reportPath)) {
    return { ok: false, reason: "missing" };
  }
  try {
    return { ok: true, report: JSON.parse(fs.readFileSync(reportPath, "utf8")) };
  } catch (error) {
    return { ok: false, reason: "unreadable", error };
  }
}

function loadReading(t) {
  for (const result of t.results ?? []) {
    for (const annotation of result.annotations ?? []) {
      if (annotation?.type !== "machine-load") continue;
      try {
        const parsed = JSON.parse(annotation.description);
        const loadavg1 = parsed?.loadavg?.[0];
        const cpus = parsed?.cpus;
        if (
          typeof loadavg1 === "number" &&
          Number.isFinite(loadavg1) &&
          Number.isInteger(cpus) &&
          cpus > 0
        ) {
          return { loadavg1, cpus, perCpu: loadavg1 / cpus };
        }
      } catch {
        continue;
      }
    }
  }
  return null;
}

export function collectOutcomes(report) {
  const specFiles = new Set();
  const entries = [];
  function walk(suite) {
    for (const child of suite.suites ?? []) walk(child);
    for (const spec of suite.specs ?? []) {
      specFiles.add(spec.file);
      for (const t of spec.tests ?? []) {
        entries.push({ file: spec.file, title: spec.title, status: t.status, load: loadReading(t) });
      }
    }
  }
  for (const suite of report.suites ?? []) walk(suite);
  return { specFiles, entries };
}
