/**
 * report-flaky.mjs sits outside vitest.config.ts's coverage include
 * (src/**\/*.{ts,tsx}) and outside eslint/tsc/knip/dep-cruiser/jscpd's scopes
 * (all scoped to src/), so nothing else in the frontend gate exercises it.
 * This file is the only thing that would catch a regression of the
 * --ratchet fail-closed behaviour (a missing/unparseable report must not
 * pass a required check having checked nothing).
 *
 * Each test spawns the real script as a subprocess with cwd pointed at a
 * fresh temp directory, never the repo root: report-flaky.mjs reads both
 * the report and e2e/flaky-baseline.json relative to cwd, and running it
 * against the repo's own checked-in baseline would couple this test to a
 * file that changes for unrelated reasons (and could drop a stray
 * playwright-results.json into the working tree).
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { buildReport as buildTypedReport } from "./playwright-report-fixture.ts";

const SCRIPT_PATH = path.resolve(process.cwd(), "scripts", "report-flaky.mjs");

let tempDir;

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "report-flaky-test-"));
});

afterEach(() => {
  fs.rmSync(tempDir, { recursive: true, force: true });
});

function writeBaseline(entries) {
  fs.mkdirSync(path.join(tempDir, "e2e"), { recursive: true });
  fs.writeFileSync(path.join(tempDir, "e2e", "flaky-baseline.json"), JSON.stringify(entries));
}

function writeReport(content) {
  fs.writeFileSync(path.join(tempDir, "playwright-results.json"), content);
}

function buildReport(entries) {
  return JSON.stringify(buildTypedReport(entries));
}

function run(args, { workspace } = {}) {
  const env = { ...process.env };
  delete env.GITHUB_WORKSPACE;
  if (workspace !== undefined) env.GITHUB_WORKSPACE = workspace;
  return spawnSync(process.execPath, [SCRIPT_PATH, ...args], { cwd: tempDir, encoding: "utf8", env });
}

function errorCommands(result) {
  return result.stderr.split("\n").filter((line) => line.startsWith("::error "));
}

function noticeCommands(result) {
  return result.stderr.split("\n").filter((line) => line.startsWith("::notice "));
}

describe("report-flaky.mjs --ratchet fails closed with no report to check", () => {
  it("exits 1 when the report file is missing", () => {
    writeBaseline([]);
    const result = run(["--ratchet"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("not found");
    expect(result.stderr).toContain("NOT a flaky test");
  });

  it("exits 1 when the report file is unparseable, including the underlying parse error", () => {
    writeBaseline([]);
    writeReport("{ not valid json");
    const result = run(["--ratchet"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("could not be read or parsed");
    expect(result.stderr).toContain("position");
    expect(result.stderr).toContain("NOT a flaky test");
  });

  it("exits 1 when the report contains a flaky spec file outside the baseline", () => {
    writeBaseline([]);
    writeReport(buildReport([{ file: "new-flake.spec.ts", status: "flaky" }]));
    const result = run(["--ratchet"]);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain("new-flake.spec.ts");
  });

  it("exits 0 on a clean report with no new flaky spec files", () => {
    writeBaseline([]);
    writeReport(buildReport([{ file: "ok.spec.ts", status: "expected" }]));
    const result = run(["--ratchet"]);
    expect(result.status).toBe(0);
  });

  it("exits 1 when the baseline file is missing (existing behaviour, guarded against regression)", () => {
    writeReport(buildReport([{ file: "ok.spec.ts", status: "expected" }]));
    const result = run(["--ratchet"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("flaky-baseline.json");
  });

  it("default (non-ratchet) mode exits 0 when the report file is missing", () => {
    const result = run([]);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("no report file found");
  });
});

describe("report-flaky.mjs --ratchet annotates each fresh flaky spec file", () => {
  it("emits one ::error workflow command per fresh flaky spec file on stderr, file-anchored under e2e/", () => {
    writeBaseline([]);
    writeReport(
      buildReport([
        { file: "other.spec.ts", status: "flaky" },
        { file: "new-flake.spec.ts", status: "flaky" },
      ]),
    );
    const result = run(["--ratchet"]);
    expect(result.status).toBe(1);
    expect(errorCommands(result)).toEqual([
      "::error file=e2e/new-flake.spec.ts,title=Flake ratchet%3A new flaky spec file::new-flake.spec.ts flaked (passed on retry) and is not in e2e/flaky-baseline.json — fix the flake; the baseline is a reviewed last resort.",
      "::error file=e2e/other.spec.ts,title=Flake ratchet%3A new flaky spec file::other.spec.ts flaked (passed on retry) and is not in e2e/flaky-baseline.json — fix the flake; the baseline is a reviewed last resort.",
    ]);
    expect(result.stdout).not.toContain("::error");
  });

  it("anchors the ::error file path relative to GITHUB_WORKSPACE when set", () => {
    writeBaseline([]);
    writeReport(buildReport([{ file: "new-flake.spec.ts", status: "flaky" }]));
    const realTemp = fs.realpathSync(tempDir);
    const result = run(["--ratchet"], { workspace: path.dirname(realTemp) });
    expect(errorCommands(result)).toEqual([
      expect.stringContaining(`::error file=${path.basename(realTemp)}/e2e/new-flake.spec.ts,title=`),
    ]);
  });

  it("escapes percent, colon and comma in the annotated path and message", () => {
    writeBaseline([]);
    writeReport(buildReport([{ file: "odd,name:100%.spec.ts", status: "flaky" }]));
    const result = run(["--ratchet"]);
    expect(errorCommands(result)).toEqual([
      expect.stringContaining("::error file=e2e/odd%2Cname%3A100%25.spec.ts,title=Flake ratchet%3A new flaky spec file::odd,name:100%25.spec.ts flaked"),
    ]);
  });

  it("emits no ::error workflow command for a baselined flaky file or a clean run", () => {
    writeBaseline(["known.spec.ts"]);
    writeReport(
      buildReport([
        { file: "known.spec.ts", status: "flaky" },
        { file: "ok.spec.ts", status: "expected" },
      ]),
    );
    const result = run(["--ratchet"]);
    expect(result.status).toBe(0);
    expect(errorCommands(result)).toEqual([]);
  });
});

function buildInterleavedReport() {
  return JSON.stringify({
    suites: [
      {
        suites: [
          {
            suites: [],
            specs: [
              { file: "a.spec.ts", title: "nested flaky test", tests: [{ status: "flaky" }] },
              { file: "a.spec.ts", title: "nested passing test", tests: [{ status: "expected" }] },
            ],
          },
        ],
        specs: [{ file: "a.spec.ts", title: "top-level failing test", tests: [{ status: "unexpected" }] }],
      },
      {
        suites: [
          {
            suites: [],
            specs: [{ file: "b.spec.ts", title: "second file flaky test", tests: [{ status: "flaky" }] }],
          },
        ],
        specs: [
          { file: "b.spec.ts", title: "second file failing test", tests: [{ status: "unexpected" }] },
          { file: "b.spec.ts", title: "second file passing test", tests: [{ status: "expected" }] },
        ],
      },
    ],
  });
}

describe("report-flaky.mjs full-output characterization (guards emission order against reordering)", () => {
  it("prints the exact default-mode stdout for an interleaved, multi-suite, multi-file report", () => {
    writeReport(buildInterleavedReport());
    const result = run([]);
    expect(result.status).toBe(0);
    expect(result.stdout).toBe(
      "### Playwright E2E flake report\n\n" +
        "- FLAKY (passed on retry): a.spec.ts › nested flaky test\n" +
        "- FAILED: a.spec.ts › top-level failing test\n" +
        "- FLAKY (passed on retry): b.spec.ts › second file flaky test\n" +
        "- FAILED: b.spec.ts › second file failing test\n",
    );
  });

  it("prints the exact --ratchet stdout/stderr and exit code for the same interleaved fixture", () => {
    writeBaseline(["b.spec.ts"]);
    writeReport(buildInterleavedReport());
    const result = run(["--ratchet"]);
    expect(result.status).toBe(1);
    expect(result.stdout).toBe(
      "### Playwright E2E flake ratchet: new flaky spec file(s)\n\n" +
        "- a.spec.ts\n\n" +
        "Fix the flake, or as a last resort add the file to e2e/flaky-baseline.json.\n",
    );
    expect(errorCommands(result)).toEqual([
      "::error file=e2e/a.spec.ts,title=Flake ratchet%3A new flaky spec file::a.spec.ts flaked (passed on retry) and is not in e2e/flaky-baseline.json — fix the flake; the baseline is a reviewed last resort.",
    ]);
    expect(result.stderr).toContain("Flake ratchet: new flaky spec file(s): a.spec.ts — details in the step summary.");
  });
});

describe("report-flaky.mjs machine-load reading", () => {
  it("renders the reading on the FLAKY row when the failing attempt carried one", () => {
    writeReport(
      buildReport([
        {
          file: "loaded.spec.ts",
          status: "flaky",
          attempts: [{ status: "failed", load: { loadavg1: 8, cpus: 4 } }, { status: "passed" }],
        },
      ]),
    );
    const result = run([]);
    expect(result.stdout).toContain(
      "- FLAKY (passed on retry): loaded.spec.ts › does a thing — load 2.00/cpu (loadavg1 8.00, cpus 4)",
    );
  });

  it("renders the FLAKY row without a load suffix when no reading is present", () => {
    writeReport(buildReport([{ file: "unloaded.spec.ts", status: "flaky" }]));
    const result = run([]);
    expect(result.stdout).toBe(
      "### Playwright E2E flake report\n\n- FLAKY (passed on retry): unloaded.spec.ts › does a thing\n",
    );
  });

  it("reads the reading from the failing attempt's own record, never the summary or the passing attempt", () => {
    writeReport(
      buildReport([
        {
          file: "trap.spec.ts",
          status: "flaky",
          attempts: [
            { status: "failed", load: { loadavg1: 8, cpus: 4 } },
            { status: "passed", load: { loadavg1: 1, cpus: 4 } },
          ],
          summaryLoad: { loadavg1: 1, cpus: 4 },
        },
      ]),
    );
    const result = run([]);
    expect(result.stdout).toContain("load 2.00/cpu (loadavg1 8.00, cpus 4)");
    expect(result.stdout).not.toContain("loadavg1 1.00");
  });

  it("appends the aggregate reading to the fresh-file ratchet bullet", () => {
    writeBaseline([]);
    writeReport(
      buildReport([
        {
          file: "loaded.spec.ts",
          status: "flaky",
          attempts: [{ status: "failed", load: { loadavg1: 8, cpus: 4 } }, { status: "passed" }],
        },
      ]),
    );
    const result = run(["--ratchet"]);
    expect(result.stdout).toContain(
      "- loaded.spec.ts — load 2.00/cpu (loadavg1 8.00, cpus 4) across 1 failing attempt(s)",
    );
  });

  it("emits a ::notice beside the ::error for a fresh flaky file that carries a reading", () => {
    writeBaseline([]);
    writeReport(
      buildReport([
        {
          file: "loaded.spec.ts",
          status: "flaky",
          attempts: [{ status: "failed", load: { loadavg1: 8, cpus: 4 } }, { status: "passed" }],
        },
      ]),
    );
    const result = run(["--ratchet"]);
    expect(noticeCommands(result)).toEqual([
      "::notice file=e2e/loaded.spec.ts,title=E2E machine load at failure::loaded.spec.ts flaked at a measured machine load of 2.00/cpu (loadavg1 8.00, cpus 4) across 1 failing attempt(s). This is an observation, not a verdict — no threshold is defined.",
    ]);
  });

  it("emits no ::notice for a fresh flaky file with no reading", () => {
    writeBaseline([]);
    writeReport(buildReport([{ file: "unloaded.spec.ts", status: "flaky" }]));
    const result = run(["--ratchet"]);
    expect(noticeCommands(result)).toEqual([]);
  });

  it("emits no ::notice at all when there are no fresh flaky files", () => {
    writeBaseline(["known.spec.ts"]);
    writeReport(
      buildReport([
        {
          file: "known.spec.ts",
          status: "flaky",
          attempts: [{ status: "failed", load: { loadavg1: 8, cpus: 4 } }, { status: "passed" }],
        },
      ]),
    );
    const result = run(["--ratchet"]);
    expect(result.status).toBe(0);
    expect(noticeCommands(result)).toEqual([]);
  });

  it("keeps the ::notice title free of the ratchet's own title marker", () => {
    writeBaseline([]);
    writeReport(
      buildReport([
        {
          file: "loaded.spec.ts",
          status: "flaky",
          attempts: [{ status: "failed", load: { loadavg1: 8, cpus: 4 } }, { status: "passed" }],
        },
      ]),
    );
    const result = run(["--ratchet"]);
    const [notice] = noticeCommands(result);
    expect(notice).not.toContain("Flake ratchet");
  });
});

describe("report-flaky.mjs exit-code invariance under a machine-load reading", () => {
  it("ratchet with no flaky files and a reading present still exits 0", () => {
    writeBaseline([]);
    writeReport(
      buildReport([
        {
          file: "known.spec.ts",
          status: "expected",
          attempts: [{ status: "passed", load: { loadavg1: 8, cpus: 4 } }],
        },
      ]),
    );
    const result = run(["--ratchet"]);
    expect(result.status).toBe(0);
  });

  it("ratchet with one fresh flaky file and a reading present still exits 1", () => {
    writeBaseline([]);
    writeReport(
      buildReport([
        {
          file: "new-flake.spec.ts",
          status: "flaky",
          attempts: [{ status: "failed", load: { loadavg1: 8, cpus: 4 } }, { status: "passed" }],
        },
      ]),
    );
    const result = run(["--ratchet"]);
    expect(result.status).toBe(1);
  });

  it("the same fresh-flaky scenario with the reading absent exits identically (1)", () => {
    writeBaseline([]);
    writeReport(buildReport([{ file: "new-flake.spec.ts", status: "flaky" }]));
    const result = run(["--ratchet"]);
    expect(result.status).toBe(1);
  });

  it("advisory mode with a reading present still exits 0", () => {
    writeReport(
      buildReport([
        {
          file: "loaded.spec.ts",
          status: "flaky",
          attempts: [{ status: "failed", load: { loadavg1: 8, cpus: 4 } }, { status: "passed" }],
        },
      ]),
    );
    const result = run([]);
    expect(result.status).toBe(0);
  });

  it("the ::error line for a fresh flaky file is byte-identical whether or not a reading is present", () => {
    writeBaseline([]);
    writeReport(buildReport([{ file: "new-flake.spec.ts", status: "flaky" }]));
    const withoutReading = run(["--ratchet"]);
    writeReport(
      buildReport([
        {
          file: "new-flake.spec.ts",
          status: "flaky",
          attempts: [{ status: "failed", load: { loadavg1: 8, cpus: 4 } }, { status: "passed" }],
        },
      ]),
    );
    const withReading = run(["--ratchet"]);
    expect(errorCommands(withReading)).toEqual(errorCommands(withoutReading));
  });
});
