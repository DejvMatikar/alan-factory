import { describe, expect, it } from "vitest";

import type { RecurringProcess } from "../api";
import { processDestination } from "./recurringLinks";

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
      detail: "x",
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
    overdue: { state: "on_time", detail: "x" },
    sub_steps: [],
    ...overrides,
  };
}

describe("processDestination", () => {
  it("links a scheduled CI workflow to its GitHub Actions run", () => {
    const destination = processDestination(process(), ENGINE_KEY);
    expect(destination).toEqual({
      kind: "external",
      href: "https://github.com/acme/widget/actions/runs/1",
      linkContext: "latest run",
    });
  });

  it("returns kind none rather than a broken href when a forge row's url is null", () => {
    const destination = processDestination(
      process({ last_run: { ...process().last_run, url: null } }),
      ENGINE_KEY,
    );
    expect(destination.kind).toBe("none");
    expect(destination.href).toBeNull();
  });

  it("links a Dependabot ecosystem update to its most recent PR", () => {
    const destination = processDestination(
      process({
        process_id: "dependency_update:pip",
        family: "dependency_update",
        last_run: { ...process().last_run, url: "https://github.com/acme/widget/pull/9" },
      }),
      ENGINE_KEY,
    );
    expect(destination).toEqual({
      kind: "external",
      href: "https://github.com/acme/widget/pull/9",
      linkContext: "latest run",
    });
  });

  it("links the triage sweep in-app to /health", () => {
    const destination = processDestination(
      process({ process_id: "triage_sweep", family: "system_sweep" }),
      ENGINE_KEY,
    );
    expect(destination).toEqual({ kind: "internal", href: "/health", linkContext: "latest run" });
  });

  it("links the daily digest in-app to /health", () => {
    const destination = processDestination(
      process({ process_id: "daily_digest", family: "system_sweep" }),
      ENGINE_KEY,
    );
    expect(destination).toEqual({ kind: "internal", href: "/health", linkContext: "latest run" });
  });

  it("links the dependency sweep to this repo's engine configuration", () => {
    const destination = processDestination(
      process({ process_id: "dependency_sweep", family: "system_sweep" }),
      ENGINE_KEY,
    );
    expect(destination).toEqual({
      kind: "internal",
      href: "/repos/acme/widget/config",
      linkContext: "configuration",
    });
  });

  it("returns kind none rather than a corrupted link when engineKey has no separator", () => {
    const destination = processDestination(
      process({ process_id: "dependency_sweep", family: "system_sweep" }),
      "malformed",
    );
    expect(destination.kind).toBe("none");
    expect(destination.href).toBeNull();
  });

  it("returns kind none rather than a corrupted link when engineKey ends with a separator", () => {
    const destination = processDestination(
      process({ process_id: "dependency_sweep", family: "system_sweep" }),
      "acme/",
    );
    expect(destination.kind).toBe("none");
    expect(destination.href).toBeNull();
  });

  it("percent-encodes owner and repo segments in the engine-configuration link", () => {
    const destination = processDestination(
      process({ process_id: "dependency_sweep", family: "system_sweep" }),
      "acme corp/widget app",
    );
    expect(destination.href).toBe("/repos/acme%20corp/widget%20app/config");
  });

  it("leaves an unrecognised system-sweep process id unlinked", () => {
    const destination = processDestination(
      process({ process_id: "something_new", family: "system_sweep" }),
      ENGINE_KEY,
    );
    expect(destination.kind).toBe("none");
  });

  it("states plainly why the state backup has no link, rather than looking broken", () => {
    const destination = processDestination(
      process({ process_id: "backup_job", family: "backup" }),
      ENGINE_KEY,
    );
    expect(destination.kind).toBe("none");
    expect(destination.href).toBeNull();
    if (destination.kind !== "none") throw new Error("expected kind none");
    expect(destination.reason).toBeTruthy();
  });
});
