/**
 * report-stress.mjs sits outside vitest.config.ts's coverage include
 * (src/**\/*.{ts,tsx}) and outside eslint/tsc/knip/dep-cruiser/jscpd's scopes
 * (all scoped to src/), so nothing else in the frontend gate exercises it.
 * This file is the only thing that would catch a regression of the
 * self-evidencing behaviour: default mode must never exit nonzero, and
 * --enforce mode must fail closed on missing/malformed data as well as on a
 * genuine count-floor breach.
 *
 * Each test spawns the real script as a subprocess with cwd pointed at a
 * fresh temp directory, never the repo root: report-stress.mjs reads both
 * the report and e2e/stress-floor.json relative to cwd, and running it
 * against the repo's own checked-in floor would couple this test to a file
 * that changes for unrelated reasons (and could drop a stray
 * playwright-results.json into the working tree).
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

const SCRIPT_PATH = path.resolve(process.cwd(), "scripts", "report-stress.mjs");

let tempDir;

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "report-stress-test-"));
});

afterEach(() => {
  fs.rmSync(tempDir, { recursive: true, force: true });
});

function writeFloor(content) {
  fs.mkdirSync(path.join(tempDir, "e2e"), { recursive: true });
  fs.writeFileSync(
    path.join(tempDir, "e2e", "stress-floor.json"),
    typeof content === "string" ? content : JSON.stringify(content),
  );
}

function writeReport(content) {
  fs.writeFileSync(
    path.join(tempDir, "playwright-results.json"),
    typeof content === "string" ? content : JSON.stringify(content),
  );
}

function buildReport({ stats, config, specs }) {
  return {
    config: config ?? {},
    stats: stats ?? {},
    suites: [
      {
        suites: [],
        specs: (specs ?? []).map((s) => ({
          file: s.file,
          title: s.title,
          tests: s.tests ?? [{ status: s.status }],
        })),
      },
    ],
  };
}

function run(args) {
  return spawnSync(process.execPath, [SCRIPT_PATH, ...args], { cwd: tempDir, encoding: "utf8" });
}

describe("report-stress.mjs is inert against the results[]-less buildReport shape", () => {
  it("exits 0 and prints identical counts regardless of the collectOutcomes load-reading extension", () => {
    writeReport(
      buildReport({
        stats: { expected: 2, unexpected: 1, flaky: 0, skipped: 0, duration: 1000 },
        specs: [
          { file: "a.spec.ts", title: "t1", status: "expected" },
          { file: "b.spec.ts", title: "t2", status: "expected" },
          { file: "c.spec.ts", title: "t3", status: "unexpected" },
        ],
      }),
    );
    const result = run([]);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("Executed: 3");
    expect(result.stdout).toContain("Passed: 2");
    expect(result.stdout).toContain("Failed: 1");
    expect(result.stdout).toContain("Failed tests:\n- FAILED: c.spec.ts › t3");
  });
});

describe("report-stress.mjs default mode never fails the job", () => {
  it("exits 0 and reports no report file found when the report is missing", () => {
    const result = run([]);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("no report file found");
  });

  it("exits 0 and reports unreadable when the report is unparseable", () => {
    writeReport("{ not valid json");
    const result = run([]);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("report file unreadable");
  });

  it("exits 0 even when the floor file is missing or malformed", () => {
    writeReport(
      buildReport({
        stats: { expected: 10, unexpected: 0, flaky: 0, skipped: 0, duration: 5000 },
        specs: [{ file: "a.spec.ts", title: "does a thing", status: "expected" }],
      }),
    );
    const result = run([]);
    expect(result.status).toBe(0);
  });

  it("prints executed, passed, failed, skipped and duration for a healthy run", () => {
    writeReport(
      buildReport({
        stats: { expected: 8, unexpected: 1, flaky: 1, skipped: 0, duration: 65_000 },
        specs: [
          { file: "a.spec.ts", title: "t1", status: "expected" },
          { file: "b.spec.ts", title: "t2", status: "unexpected" },
          { file: "c.spec.ts", title: "t3", status: "flaky" },
        ],
      }),
    );
    const result = run([]);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("Executed: 10");
    expect(result.stdout).toContain("Passed: 8");
    expect(result.stdout).toContain("Failed: 1");
    expect(result.stdout).toContain("Skipped: 0 (0 distinct tests)");
    expect(result.stdout).toContain("Spec files: 3");
    expect(result.stdout).toContain("1m 5s (65000ms)");
  });

  it("labels counts truncated when unexpected reaches config.maxFailures", () => {
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 5, flaky: 0, skipped: 0, duration: 1000 },
        config: { maxFailures: 5 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "unexpected" }],
      }),
    );
    const result = run([]);
    expect(result.stdout).toContain("counts truncated");
  });

  it("does not label counts truncated for an ordinary 1-4 failure run (default maxFailures of 5)", () => {
    writeReport(
      buildReport({
        stats: { expected: 6, unexpected: 3, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "unexpected" }],
      }),
    );
    const result = run([]);
    expect(result.stdout).not.toContain("counts truncated");
  });

  it("defaults non-numeric stats fields to 0 and flags the shape as malformed", () => {
    writeReport(
      buildReport({
        stats: { expected: "8", unexpected: 1, flaky: null, skipped: 0, duration: 65_000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "unexpected" }],
      }),
    );
    const result = run([]);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("Executed: 1");
    expect(result.stdout).toContain("stats shape malformed");
  });

  it("dedupes a --repeat-each=3 self-skipping test to one named entry while keeping the raw count", () => {
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 0, flaky: 0, skipped: 3, duration: 1000 },
        specs: [
          {
            file: "health-metrics-panel.spec.ts",
            title: "shows health metrics",
            tests: [{ status: "skipped" }, { status: "skipped" }, { status: "skipped" }],
          },
        ],
      }),
    );
    const result = run([]);
    expect(result.stdout).toContain("Skipped: 3 (1 distinct tests)");
    expect(result.stdout.match(/health-metrics-panel\.spec\.ts/g)).toHaveLength(1);
  });
});

describe("report-stress.mjs --enforce fails closed with no data to check", () => {
  it("exits 1 when the report file is missing", () => {
    writeFloor({ minExecutedTests: 1, minSpecFiles: 1 });
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("not found");
    expect(result.stderr).toContain("playwright-results.json");
  });

  it("exits 1 when the report file is unparseable", () => {
    writeFloor({ minExecutedTests: 1, minSpecFiles: 1 });
    writeReport("{ not valid json");
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("could not be read or parsed");
  });

  it("exits 1 when the floor file is missing", () => {
    writeReport(
      buildReport({
        stats: { expected: 1, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("stress-floor.json");
    expect(result.stderr).toContain("not found");
  });

  it("exits 1 when the floor file is not a JSON object", () => {
    writeFloor("[]");
    writeReport(
      buildReport({
        stats: { expected: 1, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("malformed");
  });

  it("exits 1 when the floor file is missing its numeric keys", () => {
    writeFloor({ minExecutedTests: 1 });
    writeReport(
      buildReport({
        stats: { expected: 1, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("malformed");
    expect(result.stderr).toContain("minSpecFiles");
  });

  it("exits 1 with a distinguishable message when a stats field is a non-numeric string", () => {
    writeFloor({ minExecutedTests: 1, minSpecFiles: 1 });
    writeReport(
      buildReport({
        stats: { expected: "900", unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("malformed stats shape");
  });

  it("exits 1 with a distinguishable message when a stats field is missing (would otherwise NaN every comparison)", () => {
    writeFloor({ minExecutedTests: 1, minSpecFiles: 1 });
    writeReport(
      buildReport({
        stats: { expected: 1, unexpected: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("malformed stats shape");
  });
});

describe("report-stress.mjs --enforce checks the count floor", () => {
  it("exits 1 with a distinguishable message when executed falls below minExecutedTests", () => {
    writeFloor({ minExecutedTests: 10, minSpecFiles: 1 });
    writeReport(
      buildReport({
        stats: { expected: 2, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("executed 2 < floor 10");
  });

  it("exits 1 with a distinguishable message when specFiles falls below minSpecFiles", () => {
    writeFloor({ minExecutedTests: 1, minSpecFiles: 3 });
    writeReport(
      buildReport({
        stats: { expected: 1, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("spec files executed 1 < floor 3");
  });

  it("exits 1 on a near-zero run that matches nothing", () => {
    writeFloor({ minExecutedTests: 840, minSpecFiles: 75 });
    writeReport(buildReport({ stats: { expected: 0, unexpected: 0, flaky: 0, skipped: 0, duration: 500 }, specs: [] }));
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("executed 0 < floor 840");
  });

  it("exits 0 when executed and specFiles both meet the floor", () => {
    writeFloor({ minExecutedTests: 2, minSpecFiles: 2 });
    writeReport(
      buildReport({
        stats: { expected: 2, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [
          { file: "a.spec.ts", title: "t1", status: "expected" },
          { file: "b.spec.ts", title: "t2", status: "expected" },
        ],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("Executed: 2");
  });

  it("exits 0 when executed exactly meets minExecutedTests", () => {
    writeFloor({ minExecutedTests: 3, minSpecFiles: 1 });
    writeReport(
      buildReport({
        stats: { expected: 3, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(0);
  });

  it("exits 1 when executed is exactly one below minExecutedTests", () => {
    writeFloor({ minExecutedTests: 3, minSpecFiles: 1 });
    writeReport(
      buildReport({
        stats: { expected: 2, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run(["--enforce"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("executed 2 < floor 3");
  });
});

describe("report-stress.mjs default mode names failed tests", () => {
  it("prints no Failed tests block when there are zero failures", () => {
    writeReport(
      buildReport({
        stats: { expected: 2, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = run([]);
    expect(result.stdout).not.toContain("Failed tests:");
  });

  it("names a single failed test by spec file and title", () => {
    writeReport(
      buildReport({
        stats: { expected: 1, unexpected: 1, flaky: 0, skipped: 0, duration: 1000 },
        specs: [
          { file: "a.spec.ts", title: "passes", status: "expected" },
          { file: "b.spec.ts", title: "breaks", status: "unexpected" },
        ],
      }),
    );
    const result = run([]);
    expect(result.stdout).toContain("Failed tests:\n- FAILED: b.spec.ts › breaks");
  });

  it("names several failed tests across multiple spec files, sorted", () => {
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 3, flaky: 0, skipped: 0, duration: 1000 },
        specs: [
          { file: "z.spec.ts", title: "z breaks", status: "unexpected" },
          { file: "a.spec.ts", title: "a breaks", status: "unexpected" },
          { file: "m.spec.ts", title: "m breaks", status: "unexpected" },
        ],
      }),
    );
    const result = run([]);
    const failedSection = result.stdout.slice(result.stdout.indexOf("Failed tests:"));
    expect(failedSection).toBe(
      "Failed tests:\n" +
        "- FAILED: a.spec.ts › a breaks\n" +
        "- FAILED: m.spec.ts › m breaks\n" +
        "- FAILED: z.spec.ts › z breaks\n",
    );
  });

  it("dedupes a test failing on all 3 repeats to exactly one FAILED row", () => {
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 3, flaky: 0, skipped: 0, duration: 1000 },
        specs: [
          {
            file: "flaky-repeat.spec.ts",
            title: "fails every repeat",
            tests: [{ status: "unexpected" }, { status: "unexpected" }, { status: "unexpected" }],
          },
        ],
      }),
    );
    const result = run([]);
    expect(result.stdout.match(/- FAILED: flaky-repeat\.spec\.ts/g)).toHaveLength(1);
  });

  it("keeps named failures adjacent to, and below, the truncation notice", () => {
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 5, flaky: 0, skipped: 0, duration: 1000 },
        config: { maxFailures: 5 },
        specs: [
          { file: "a.spec.ts", title: "t1", status: "unexpected" },
          { file: "b.spec.ts", title: "t2", status: "unexpected" },
        ],
      }),
    );
    const result = run([]);
    expect(result.stdout).toContain(
      "_counts truncated — run stopped early after hitting maxFailures_\n\nFailed tests:\n- FAILED: a.spec.ts › t1\n- FAILED: b.spec.ts › t2",
    );
  });

  it("keeps counts, duration, spec-file count and named self-skips unchanged when failures are also present", () => {
    writeReport(
      buildReport({
        stats: { expected: 5, unexpected: 1, flaky: 1, skipped: 3, duration: 65_000 },
        specs: [
          { file: "a.spec.ts", title: "passes", status: "expected" },
          { file: "b.spec.ts", title: "breaks", status: "unexpected" },
          { file: "c.spec.ts", title: "flakes", status: "flaky" },
          {
            file: "health-metrics-panel.spec.ts",
            title: "shows health metrics",
            tests: [{ status: "skipped" }, { status: "skipped" }, { status: "skipped" }],
          },
        ],
      }),
    );
    const result = run([]);
    expect(result.stdout).toContain("Executed: 7");
    expect(result.stdout).toContain("Passed: 5");
    expect(result.stdout).toContain("Failed: 1");
    expect(result.stdout).toContain("Skipped: 3 (1 distinct tests)");
    expect(result.stdout).toContain("Spec files: 4");
    expect(result.stdout).toContain("1m 5s (65000ms)");
    expect(result.stdout).toContain("Failed tests:\n- FAILED: b.spec.ts › breaks");
    expect(result.stdout).toContain("Skipped tests:\n- health-metrics-panel.spec.ts › shows health metrics");
  });
});

describe("report-stress.mjs --fingerprint", () => {
  function hexDigestArgs() {
    return run(["--fingerprint"]);
  }

  it("prints empty stdout and exits 0 when the report is missing", () => {
    const result = hexDigestArgs();
    expect(result.status).toBe(0);
    expect(result.stdout).toBe("");
    expect(result.stdout).not.toContain("Playwright E2E stress sweep");
  });

  it("prints empty stdout and exits 0 when the report is malformed JSON", () => {
    writeReport("{ not valid json");
    const result = hexDigestArgs();
    expect(result.status).toBe(0);
    expect(result.stdout).toBe("");
  });

  it("prints empty stdout and exits 0 when there are zero failures", () => {
    writeReport(
      buildReport({
        stats: { expected: 3, unexpected: 0, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "t1", status: "expected" }],
      }),
    );
    const result = hexDigestArgs();
    expect(result.status).toBe(0);
    expect(result.stdout).toBe("");
  });

  it("prints a stable, non-empty marker for a failing run and never the default summary", () => {
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 1, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "breaks", status: "unexpected" }],
      }),
    );
    const result = hexDigestArgs();
    expect(result.status).toBe(0);
    expect(result.stdout.trim()).toMatch(/^stress-fail-1-[0-9a-f]{12}$/);
    expect(result.stdout).not.toContain("Playwright E2E stress sweep");
  });

  it("is stable across a reordering of suites and specs in the report", () => {
    const reportA = {
      config: {},
      stats: {},
      suites: [
        {
          suites: [],
          specs: [
            { file: "a.spec.ts", title: "t1", tests: [{ status: "unexpected" }] },
            { file: "b.spec.ts", title: "t2", tests: [{ status: "unexpected" }] },
          ],
        },
        { suites: [], specs: [{ file: "c.spec.ts", title: "t3", tests: [{ status: "unexpected" }] }] },
      ],
    };
    const reportB = {
      config: {},
      stats: {},
      suites: [
        { suites: [], specs: [{ file: "c.spec.ts", title: "t3", tests: [{ status: "unexpected" }] }] },
        {
          suites: [],
          specs: [
            { file: "b.spec.ts", title: "t2", tests: [{ status: "unexpected" }] },
            { file: "a.spec.ts", title: "t1", tests: [{ status: "unexpected" }] },
          ],
        },
      ],
    };
    writeReport(reportA);
    const first = hexDigestArgs();
    writeReport(reportB);
    const second = hexDigestArgs();
    expect(first.stdout).not.toBe("");
    expect(first.stdout).toBe(second.stdout);
  });

  it("differs when the failing set differs", () => {
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 1, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "breaks", status: "unexpected" }],
      }),
    );
    const first = hexDigestArgs();
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 1, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "z.spec.ts", title: "breaks differently", status: "unexpected" }],
      }),
    );
    const second = hexDigestArgs();
    expect(first.stdout).not.toBe(second.stdout);
  });

  it("differs when the failure count differs even though the sets overlap", () => {
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 1, flaky: 0, skipped: 0, duration: 1000 },
        specs: [{ file: "a.spec.ts", title: "breaks", status: "unexpected" }],
      }),
    );
    const first = hexDigestArgs();
    writeReport(
      buildReport({
        stats: { expected: 0, unexpected: 2, flaky: 0, skipped: 0, duration: 1000 },
        specs: [
          { file: "a.spec.ts", title: "breaks", status: "unexpected" },
          { file: "b.spec.ts", title: "also breaks", status: "unexpected" },
        ],
      }),
    );
    const second = hexDigestArgs();
    expect(first.stdout).not.toBe(second.stdout);
  });

  it("is identical for two runs whose full failed sets match but were both maxFailures-truncated to the same first 5", () => {
    const specs = ["a", "b", "c", "d", "e"].map((letter) => ({
      file: `${letter}.spec.ts`,
      title: "breaks",
      tests: [{ status: "unexpected" }],
    }));
    writeReport({ config: { maxFailures: 5 }, stats: {}, suites: [{ suites: [], specs }] });
    const first = hexDigestArgs();
    writeReport({ config: { maxFailures: 5 }, stats: {}, suites: [{ suites: [], specs: [...specs].reverse() }] });
    const second = hexDigestArgs();
    expect(first.stdout).not.toBe("");
    expect(first.stdout).toBe(second.stdout);
  });
});
