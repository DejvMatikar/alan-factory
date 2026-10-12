import { describe, expect, it } from "vitest";

import type {
  RecurringBackupCoverage,
  RecurringCadence,
  RecurringProcess,
  RecurringRunState,
  RecurringStatus,
} from "../api";
import {
  backupCoverageText,
  cadenceText,
  durationText,
  failingRows,
  formatCadenceInterval,
  lastRunText,
  mergedSummaryLine,
  outcomeText,
  overdueText,
  overdueTone,
  recurringRows,
  runStateText,
  runStateTone,
  summaryCounts,
  summaryLine,
  toRecurringRow,
} from "./recurringFields";

const ENGINE_KEY = "acme/widget";

function process(overrides: Partial<RecurringProcess> = {}): RecurringProcess {
  return {
    process_id: "scheduled_ci:1",
    family: "scheduled_ci",
    label: "CI workflow",
    cadence: {
      kind: "cron",
      interval_seconds: 1800,
      cron_expression: "*/30 * * * *",
      rides_on: null,
      detail: "parsed from this repo's own workflow configuration",
    },
    last_run: {
      provenance: { status: "fresh", detail: "" },
      at: "2026-08-17T12:00:00Z",
      state: "succeeded",
      outcome: "success",
      url: "https://github.com/acme/widget/actions/runs/1",
    },
    duration_state: "measured",
    duration_seconds: 120,
    overdue: { state: "on_time", detail: "last ran 60s ago; expected every 1800s" },
    sub_steps: [],
    ...overrides,
  };
}

function statusWith(processes: RecurringProcess[]): RecurringStatus {
  return {
    slug: "s",
    is_default: false,
    processes,
    service_scope_note: null,
    backup_coverage: {
      provenance: { status: "unknown", detail: "" },
      included_in_last_snapshot: null,
      changed_since_count: null,
      excluded_caveat: "x",
    },
  };
}

describe("formatCadenceInterval", () => {
  it("renders a plural week count", () => {
    expect(formatCadenceInterval(2629800)).toBe("4 weeks");
  });

  it("renders a singular week", () => {
    expect(formatCadenceInterval(604800)).toBe("1 week");
  });

  it("renders a singular day", () => {
    expect(formatCadenceInterval(86400)).toBe("1 day");
  });

  it("renders a plural hour count", () => {
    expect(formatCadenceInterval(21600)).toBe("6 hours");
  });

  it("renders a singular hour", () => {
    expect(formatCadenceInterval(3600)).toBe("1 hour");
  });

  it("renders a plural minute count", () => {
    expect(formatCadenceInterval(1800)).toBe("30 minutes");
  });

  it("renders a singular minute", () => {
    expect(formatCadenceInterval(60)).toBe("1 minute");
  });

  it("falls back to raw seconds below a minute", () => {
    expect(formatCadenceInterval(30)).toBe("30s");
  });
});

describe("cadenceText", () => {
  function cadence(overrides: Partial<RecurringCadence> = {}): RecurringCadence {
    return {
      kind: "interval_seconds",
      interval_seconds: 1800,
      cron_expression: null,
      rides_on: null,
      detail: "fixed 30-minute sweep cadence",
      ...overrides,
    };
  }

  it("renders a manual cadence with its detail", () => {
    expect(cadenceText(cadence({ kind: "manual", interval_seconds: null, detail: "triggered by hand only" }))).toBe(
      "Manual only — triggered by hand only",
    );
  });

  it("renders a rides_on cadence naming the host process", () => {
    expect(
      cadenceText(
        cadence({ kind: "rides_on", interval_seconds: null, rides_on: "triage_sweep", detail: "generated daily" }),
      ),
    ).toBe("Rides on triage_sweep — generated daily");
  });

  it("falls back to a generic host name when rides_on carries no value", () => {
    expect(cadenceText(cadence({ kind: "rides_on", interval_seconds: null, rides_on: null, detail: "x" }))).toBe(
      "Rides on another process — x",
    );
  });

  it("renders a formatted interval when interval_seconds is present", () => {
    expect(cadenceText(cadence({ interval_seconds: 1800 }))).toBe("Every 30 minutes");
  });

  it("renders the raw cron expression when interval_seconds could not be parsed", () => {
    expect(
      cadenceText(
        cadence({
          kind: "cron",
          interval_seconds: null,
          cron_expression: "1,2,3 * * * *",
          detail: "cron expression could not be parsed",
        }),
      ),
    ).toBe("Cron `1,2,3 * * * *` — cron expression could not be parsed");
  });

  it("falls back to the plain detail when neither interval nor cron expression is available", () => {
    expect(
      cadenceText(cadence({ kind: "cron", interval_seconds: null, cron_expression: null, detail: "no data" })),
    ).toBe("no data");
  });
});

describe("lastRunText", () => {
  it("renders a relative time when a run was observed", () => {
    expect(
      lastRunText(
        process({
          last_run: {
            provenance: { status: "fresh", detail: "" },
            at: "1970-01-01T00:00:00Z",
            state: "succeeded",
            outcome: "success",
            url: null,
          },
        }),
      ),
    ).not.toBe("");
  });

  it("renders never-observed with the provenance detail when a reason is given", () => {
    expect(
      lastRunText(
        process({
          last_run: {
            provenance: { status: "unknown", detail: "no credential available" },
            at: null,
            state: "unknown",
            outcome: null,
            url: null,
          },
        }),
      ),
    ).toBe("Never observed — no credential available");
  });

  it("falls back to a generic reason when the provenance detail is blank", () => {
    expect(
      lastRunText(
        process({
          last_run: {
            provenance: { status: "unknown", detail: "" },
            at: null,
            state: "unknown",
            outcome: null,
            url: null,
          },
        }),
      ),
    ).toBe("Never observed — no run recorded yet");
  });
});

describe("outcomeText", () => {
  const CASES: readonly [RecurringRunState, string][] = [
    ["succeeded", "Succeeded"],
    ["failed", "Failed"],
    ["attention", "Needs a person"],
    ["cancelled", "Ended without completing"],
    ["in_progress", "Currently running"],
    ["queued", "Queued to run"],
    ["never_ran", "Never run yet"],
    ["unknown", "Outcome unknown"],
  ];

  it.each(CASES)("reports %s as %s", (state, expected) => {
    expect(outcomeText(process({ last_run: { ...process().last_run, state, outcome: null } }))).toBe(expected);
  });

  it("reports the degraded middle state distinctly from a generic attention reading", () => {
    expect(
      outcomeText(process({ last_run: { ...process().last_run, state: "attention", outcome: "degraded" } })),
    ).toBe("Completed with degradation");
  });
});

describe("durationText", () => {
  it("renders a formatted duration when measured", () => {
    expect(durationText(process({ duration_state: "measured", duration_seconds: 120 }))).toBe("2m");
  });

  it("renders a dash when measured but no seconds were recorded", () => {
    expect(durationText(process({ duration_state: "measured", duration_seconds: null }))).toBe("—");
  });

  it("renders the daily-digest-specific not-applicable reason", () => {
    expect(
      durationText(process({ process_id: "daily_digest", duration_state: "not_applicable", duration_seconds: null })),
    ).toBe("Not applicable — it is generated by the triage sweep, not run on its own");
  });

  it("renders the generic not-applicable reason for every other process", () => {
    expect(
      durationText(process({ process_id: "dependency_update:npm", duration_state: "not_applicable", duration_seconds: null })),
    ).toBe("Not applicable — this kind of process exposes no run duration to measure");
  });

  it("renders unknown with the last-run provenance detail as the reason", () => {
    expect(
      durationText(
        process({
          duration_state: "unknown",
          duration_seconds: null,
          last_run: {
            provenance: { status: "unknown", detail: "no triage sweep has completed yet" },
            at: null,
            state: "unknown",
            outcome: null,
            url: null,
          },
        }),
      ),
    ).toBe("Unknown — no triage sweep has completed yet");
  });

  it("falls back to a generic unknown reason when the provenance detail is blank", () => {
    expect(
      durationText(
        process({
          duration_state: "unknown",
          duration_seconds: null,
          last_run: {
            provenance: { status: "unknown", detail: "" },
            at: null,
            state: "unknown",
            outcome: null,
            url: null,
          },
        }),
      ),
    ).toBe("Unknown — duration not recorded");
  });
});

describe("overdueTone", () => {
  it("maps overdue to transitional, not red — red is reserved for run-state failures", () => {
    expect(overdueTone("overdue")).toBe("transitional");
  });

  it("maps on_time to green", () => {
    expect(overdueTone("on_time")).toBe("green");
  });

  it("maps unknown to unknown", () => {
    expect(overdueTone("unknown")).toBe("unknown");
  });
});

describe("runStateTone / runStateText", () => {
  const TONE_TABLE: readonly [RecurringRunState, string][] = [
    ["succeeded", "green"],
    ["failed", "red"],
    ["attention", "transitional"],
    ["cancelled", "unknown"],
    ["in_progress", "unknown"],
    ["queued", "unknown"],
    ["never_ran", "unknown"],
    ["unknown", "unknown"],
  ];

  it.each(TONE_TABLE)("maps %s to the %s tone", (state, expected) => {
    expect(runStateTone(state)).toBe(expected);
  });

  it("gives every tone='unknown' state its own distinct label text", () => {
    const unknownToned = TONE_TABLE.filter(([, tone]) => tone === "unknown").map(([state]) => state);
    const labels = unknownToned.map(runStateText);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toEqual(["Cancelled", "Running", "Queued", "Never run", "Unknown"]);
  });

  it("gives succeeded/failed/attention their own distinct label text too", () => {
    expect(runStateText("succeeded")).toBe("Succeeded");
    expect(runStateText("failed")).toBe("Failed");
    expect(runStateText("attention")).toBe("Needs attention");
  });
});

describe("overdueText", () => {
  it("labels an overdue process with its detail", () => {
    expect(overdueText(process({ overdue: { state: "overdue", detail: "last ran too long ago" } }))).toBe(
      "Overdue — last ran too long ago",
    );
  });

  it("labels an on-time process with its detail", () => {
    expect(overdueText(process({ overdue: { state: "on_time", detail: "within cadence" } }))).toBe(
      "On time — within cadence",
    );
  });

  it("labels an unknown-overdue process with its detail", () => {
    expect(overdueText(process({ overdue: { state: "unknown", detail: "no fixed interval" } }))).toBe(
      "Unknown — no fixed interval",
    );
  });
});

describe("toRecurringRow / recurringRows", () => {
  it("maps a leaf process with no sub-steps to an empty subRows array", () => {
    const row = toRecurringRow(process(), ENGINE_KEY);
    expect(row.processId).toBe("scheduled_ci:1");
    expect(row.subRows).toEqual([]);
  });

  it("carries the process's runState and destination through to the row", () => {
    const row = toRecurringRow(process(), ENGINE_KEY);
    expect(row.runState).toBe("succeeded");
    expect(row.destination).toEqual({
      kind: "external",
      href: "https://github.com/acme/widget/actions/runs/1",
      linkContext: "latest run",
    });
  });

  it("recursively maps nested sub-steps, e.g. the daily digest riding on the triage sweep", () => {
    const digest = process({
      process_id: "daily_digest",
      family: "system_sweep",
      label: "Daily digest",
      cadence: { kind: "rides_on", interval_seconds: null, cron_expression: null, rides_on: "triage_sweep", detail: "x" },
      duration_state: "not_applicable",
      duration_seconds: null,
    });
    const sweep = process({
      process_id: "triage_sweep",
      family: "system_sweep",
      label: "Failure triage sweep",
      sub_steps: [digest],
    });
    const row = toRecurringRow(sweep, ENGINE_KEY);
    expect(row.subRows).toHaveLength(1);
    expect(row.subRows[0]!.processId).toBe("daily_digest");
    expect(row.subRows[0]!.destination).toEqual({ kind: "internal", href: "/health", linkContext: "latest run" });
  });

  it("maps every process in a RecurringStatus payload", () => {
    const status = statusWith([process({ process_id: "a" }), process({ process_id: "b" })]);
    expect(recurringRows(status, ENGINE_KEY).map((r) => r.processId)).toEqual(["a", "b"]);
  });
});

describe("failingRows", () => {
  it("returns only rows whose runState is failed, flattened across sub-steps", () => {
    const digest = process({
      process_id: "daily_digest",
      last_run: { ...process().last_run, state: "failed" },
    });
    const sweep = process({
      process_id: "triage_sweep",
      last_run: { ...process().last_run, state: "attention" },
      sub_steps: [digest],
    });
    const ok = process({ process_id: "ok" });
    const rows = recurringRows(statusWith([sweep, ok]), ENGINE_KEY);
    expect(failingRows(rows).map((r) => r.processId)).toEqual(["daily_digest"]);
  });

  it("returns an empty array when nothing failed", () => {
    const rows = recurringRows(statusWith([process()]), ENGINE_KEY);
    expect(failingRows(rows)).toEqual([]);
  });
});

describe("summaryCounts / summaryLine", () => {
  it("counts zero overdue and zero attention with no extra summary segments", () => {
    const rows = recurringRows(statusWith([process()]), ENGINE_KEY);
    const counts = summaryCounts(rows);
    expect(counts).toEqual({ total: 1, overdue: 0, failed: 0, attention: 0 });
    expect(summaryLine(counts)).toBe("1 process");
  });

  it("folds failed and attention rows into a single attention figure in the summary line", () => {
    const rows = recurringRows(
      statusWith([
        process({ process_id: "a", overdue: { state: "overdue", detail: "x" } }),
        process({ process_id: "b", last_run: { ...process().last_run, state: "failed" } }),
        process({ process_id: "c", last_run: { ...process().last_run, state: "attention" } }),
        process({ process_id: "d" }),
      ]),
      ENGINE_KEY,
    );
    const counts = summaryCounts(rows);
    expect(counts).toEqual({ total: 4, overdue: 1, failed: 1, attention: 2 });
    expect(summaryLine(counts)).toBe("4 processes · 1 overdue · 2 need attention");
  });

  it("does not let cancelled or skipped rows inflate the failed count", () => {
    const rows = recurringRows(
      statusWith([
        process({ process_id: "a", last_run: { ...process().last_run, state: "cancelled" } }),
        process({ process_id: "b", last_run: { ...process().last_run, state: "queued" } }),
      ]),
      ENGINE_KEY,
    );
    const counts = summaryCounts(rows);
    expect(counts.failed).toBe(0);
    expect(counts.attention).toBe(0);
  });

  it("counts nested sub-steps so the summary matches what expanding the disclosure reveals", () => {
    const digest = process({
      process_id: "daily_digest",
      overdue: { state: "overdue", detail: "x" },
      last_run: { ...process().last_run, state: "failed" },
    });
    const sweep = process({ process_id: "triage_sweep", sub_steps: [digest] });
    const rows = recurringRows(statusWith([sweep]), ENGINE_KEY);
    const counts = summaryCounts(rows);
    expect(counts).toEqual({ total: 2, overdue: 1, failed: 1, attention: 1 });
    expect(summaryLine(counts)).toBe("2 processes · 1 overdue · 1 need attention");
  });
});

describe("mergedSummaryLine", () => {
  it("falls back to the plain summary line when nothing failed", () => {
    const rows = recurringRows(statusWith([process()]), ENGINE_KEY);
    const counts = summaryCounts(rows);
    expect(mergedSummaryLine(counts, rows)).toBe("1 process");
  });

  it("appends a Failing clause naming every failure up to the cap", () => {
    const rows = recurringRows(
      statusWith([
        process({ process_id: "a", label: "Nightly build", last_run: { ...process().last_run, state: "failed" } }),
        process({ process_id: "b", label: "Weekly audit", last_run: { ...process().last_run, state: "failed" } }),
      ]),
      ENGINE_KEY,
    );
    const counts = summaryCounts(rows);
    expect(mergedSummaryLine(counts, rows)).toBe(
      "2 processes · 2 need attention · Failing: Nightly build, Weekly audit",
    );
  });

  it("caps named failures at three and appends a +N more count", () => {
    const rows = recurringRows(
      statusWith(
        [1, 2, 3, 4, 5].map((n) =>
          process({
            process_id: `p${String(n)}`,
            label: `Job ${String(n)}`,
            last_run: { ...process().last_run, state: "failed" },
          }),
        ),
      ),
      ENGINE_KEY,
    );
    const counts = summaryCounts(rows);
    expect(mergedSummaryLine(counts, rows)).toBe(
      "5 processes · 5 need attention · Failing: Job 1, Job 2, Job 3 +2 more",
    );
  });
});

describe("backupCoverageText", () => {
  function coverage(overrides: Partial<RecurringBackupCoverage> = {}): RecurringBackupCoverage {
    return {
      provenance: { status: "fresh", detail: "most recent backup_receipt.json" },
      included_in_last_snapshot: null,
      changed_since_count: null,
      excluded_caveat: "per-repository engine configuration is structurally excluded",
      ...overrides,
    };
  }

  it("reports coverage with a plural change count when the last snapshot succeeded", () => {
    expect(
      backupCoverageText(coverage({ included_in_last_snapshot: true, changed_since_count: 3 })),
    ).toBe(
      "Covered by the most recent backup snapshot · 3 changes since. per-repository engine configuration is structurally excluded.",
    );
  });

  it("reports coverage with a singular change count when exactly one workspace changed", () => {
    expect(
      backupCoverageText(coverage({ included_in_last_snapshot: true, changed_since_count: 1 })),
    ).toBe(
      "Covered by the most recent backup snapshot · 1 change since. per-repository engine configuration is structurally excluded.",
    );
  });

  it("reports an explicit zero-changes clause distinct from the unknown clause", () => {
    expect(
      backupCoverageText(coverage({ included_in_last_snapshot: true, changed_since_count: 0 })),
    ).toBe(
      "Covered by the most recent backup snapshot · 0 changes since. per-repository engine configuration is structurally excluded.",
    );
  });

  it("renders the changes-since figure as unknown, with no explanatory detail, when it could not be computed", () => {
    expect(
      backupCoverageText(coverage({ included_in_last_snapshot: true, changed_since_count: null })),
    ).toBe(
      "Covered by the most recent backup snapshot · changes since unknown. per-repository engine configuration is structurally excluded.",
    );
  });

  it("reports non-coverage with the provenance detail when the last backup did not succeed", () => {
    expect(
      backupCoverageText(
        coverage({
          included_in_last_snapshot: false,
          provenance: { status: "unknown", detail: "most recent backup run did not succeed" },
        }),
      ),
    ).toBe(
      "Not covered by the most recent backup — most recent backup run did not succeed. per-repository engine configuration is structurally excluded.",
    );
  });

  it("reports unknown coverage with the provenance detail when no receipt has ever been written", () => {
    expect(
      backupCoverageText(
        coverage({ included_in_last_snapshot: null, provenance: { status: "unknown", detail: "no backup receipt found yet" } }),
      ),
    ).toBe(
      "Backup coverage unknown — no backup receipt found yet. per-repository engine configuration is structurally excluded.",
    );
  });
});
