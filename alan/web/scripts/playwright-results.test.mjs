import { describe, expect, it } from "vitest";

import { collectOutcomes } from "./playwright-results.mjs";
import { buildReport } from "./playwright-report-fixture.ts";

describe("collectOutcomes load reading extraction", () => {
  it("extracts the reading from a single failing attempt", () => {
    const report = buildReport([
      {
        file: "a.spec.ts",
        status: "unexpected",
        attempts: [{ status: "failed", load: { loadavg1: 6, cpus: 3 } }],
      },
    ]);
    const { entries } = collectOutcomes(report);
    expect(entries).toEqual([
      { file: "a.spec.ts", title: "does a thing", status: "unexpected", load: { loadavg1: 6, cpus: 3, perCpu: 2 } },
    ]);
  });

  it("prefers the failing attempt's own reading over a summary-level or later-attempt reading", () => {
    const report = buildReport([
      {
        file: "a.spec.ts",
        status: "flaky",
        attempts: [
          { status: "failed", load: { loadavg1: 8, cpus: 4 } },
          { status: "passed", load: { loadavg1: 1, cpus: 4 } },
        ],
        summaryLoad: { loadavg1: 1, cpus: 4 },
      },
    ]);
    const { entries } = collectOutcomes(report);
    expect(entries[0].load).toEqual({ loadavg1: 8, cpus: 4, perCpu: 2 });
  });

  it("is null with no throw when no annotation is present anywhere", () => {
    const report = buildReport([{ file: "a.spec.ts", status: "expected" }]);
    const { entries } = collectOutcomes(report);
    expect(entries[0].load).toBeNull();
  });

  it("is null with no throw when the annotation description is malformed", () => {
    const report = buildReport([{ file: "a.spec.ts", status: "unexpected" }]);
    report.suites[0].specs[0].tests[0].results[0].annotations = [
      { type: "machine-load", description: "{ not valid json" },
    ];
    const { entries } = collectOutcomes(report);
    expect(entries[0].load).toBeNull();
  });

  it("is null with no throw when a report has no results[] key at all", () => {
    const report = {
      suites: [
        {
          suites: [],
          specs: [{ file: "a.spec.ts", title: "t", tests: [{ status: "expected" }] }],
        },
      ],
    };
    const { entries } = collectOutcomes(report);
    expect(entries).toEqual([{ file: "a.spec.ts", title: "t", status: "expected", load: null }]);
  });

  it("still yields file, title and status unchanged when descending nested suites", () => {
    const report = {
      suites: [
        {
          suites: [
            {
              suites: [],
              specs: [{ file: "a.spec.ts", title: "nested", tests: [{ status: "expected" }] }],
            },
          ],
          specs: [],
        },
      ],
    };
    const { entries, specFiles } = collectOutcomes(report);
    expect(entries).toEqual([{ file: "a.spec.ts", title: "nested", status: "expected", load: null }]);
    expect(specFiles).toEqual(new Set(["a.spec.ts"]));
  });
});
