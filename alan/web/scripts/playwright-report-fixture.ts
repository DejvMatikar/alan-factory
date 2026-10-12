import type {
  JSONReport,
  JSONReportSuite,
  JSONReportTest,
  JSONReportTestResult,
} from "@playwright/test/reporter";

export interface FixtureLoadReading {
  loadavg1: number;
  cpus: number;
}

export interface FixtureAttempt {
  status: JSONReportTestResult["status"];
  load?: FixtureLoadReading;
}

export interface FixtureEntry {
  file: string;
  title?: string;
  status: JSONReportTest["status"];
  attempts?: FixtureAttempt[];
  summaryLoad?: FixtureLoadReading;
}

function loadAnnotation(reading: FixtureLoadReading | undefined) {
  if (!reading) return [];
  return [
    {
      type: "machine-load",
      description: JSON.stringify({ loadavg: [reading.loadavg1, 0, 0], cpus: reading.cpus }),
    },
  ];
}

function buildResult(attempt: FixtureAttempt, index: number): JSONReportTestResult {
  return {
    workerIndex: 0,
    parallelIndex: 0,
    status: attempt.status,
    duration: 0,
    error: undefined,
    errors: [],
    stdout: [],
    stderr: [],
    retry: index,
    startTime: new Date(0).toISOString(),
    attachments: [],
    annotations: loadAnnotation(attempt.load),
  };
}

const DEFAULT_ATTEMPT_STATUS: Record<JSONReportTest["status"], JSONReportTestResult["status"]> = {
  expected: "passed",
  unexpected: "failed",
  flaky: "failed",
  skipped: "skipped",
};

function buildTest(entry: FixtureEntry): JSONReportTest {
  const attempts = entry.attempts ?? [{ status: DEFAULT_ATTEMPT_STATUS[entry.status] }];
  return {
    timeout: 60_000,
    annotations: loadAnnotation(entry.summaryLoad),
    expectedStatus: "passed",
    projectName: "chromium",
    projectId: "chromium",
    results: attempts.map(buildResult),
    status: entry.status,
  };
}

export function buildReport(entries: FixtureEntry[]): JSONReport {
  const suite: JSONReportSuite = {
    title: "",
    file: "",
    column: 0,
    line: 0,
    suites: [],
    specs: entries.map((e) => ({
      tags: [],
      title: e.title ?? "does a thing",
      ok: e.status !== "unexpected",
      tests: [buildTest(e)],
      id: "",
      file: e.file,
      line: 0,
      column: 0,
    })),
  };
  return {
    config: {
      projects: [],
    } as unknown as JSONReport["config"],
    suites: [suite],
    errors: [],
    stats: {
      startTime: new Date(0).toISOString(),
      duration: 0,
      expected: 0,
      unexpected: 0,
      flaky: 0,
      skipped: 0,
    },
  };
}
