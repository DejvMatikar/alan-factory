import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, expectTypeOf, it } from "vitest";

import {
  ApiError,
  api,
  type RecurringCadenceKind,
  type RecurringDurationState,
  type RecurringOverdueState,
  type RecurringProcess,
  type RecurringProcessFamily,
  type RecurringRunState,
  type RecurringStatus,
} from "./api";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { server } from "./test/server";

const OWNER = "org-1";
const REPO = "svc.core";
const SLUG = `${OWNER}/${REPO}`;
const RECURRING_PATH = `/api/engines/${OWNER}/${REPO}/recurring-status`;

const STALE_PROVENANCE = { status: "stale", detail: "last refresh failed" } as const;

function buildProcess(overrides: Partial<RecurringProcess> = {}): RecurringProcess {
  return {
    process_id: "nightly-ci",
    family: "scheduled_ci",
    label: "Nightly CI",
    cadence: {
      kind: "cron",
      interval_seconds: null,
      cron_expression: "0 21 * * *",
      rides_on: null,
      detail: "daily at 21:00 UTC",
    },
    last_run: {
      provenance: STALE_PROVENANCE,
      at: "2026-09-19T21:00:00Z",
      state: "succeeded",
      outcome: "success",
      url: "https://github.com/org-1/svc.core/actions/runs/1",
    },
    duration_state: "measured",
    duration_seconds: 312,
    overdue: { state: "on_time", detail: "ran within its window" },
    sub_steps: [],
    ...overrides,
  };
}

function buildWireStatus(processes: RecurringProcess[]): RecurringStatus {
  return {
    slug: SLUG,
    is_default: false,
    processes,
    service_scope_note: null,
    backup_coverage: {
      provenance: STALE_PROVENANCE,
      included_in_last_snapshot: null,
      changed_since_count: null,
      excluded_caveat: "logs are not backed up",
    },
  };
}

let arrivals: Request[] = [];

function serveRecurring(respond: () => Response): void {
  server.use(
    http.get("/api/engines/:owner/:repo/recurring-status", ({ request }) => {
      arrivals.push(request);
      return respond();
    }),
  );
}

beforeEach(() => {
  arrivals = [];
  clearControlToken();
});

afterEach(() => {
  clearControlToken();
});

describe("recurring-status read", () => {
  it("resolves to the parsed body, sub-steps and null fields intact", async () => {
    const wire = buildWireStatus([
      buildProcess({ sub_steps: [buildProcess({ process_id: "nightly-ci-lint", label: "Lint" })] }),
    ]);
    serveRecurring(() => HttpResponse.json(wire));
    const status = await api.getRecurringStatus(SLUG);
    expect(status).toEqual(wire);
    expect(status.service_scope_note).toBeNull();
    expect(status.processes[0]?.sub_steps).toHaveLength(1);
  });

  it("issues exactly one GET whose path carries the segments literally", async () => {
    serveRecurring(() => HttpResponse.json(buildWireStatus([])));
    await api.getRecurringStatus(SLUG);
    expect(arrivals.map((seen) => [seen.method, new URL(seen.url).pathname])).toEqual([
      ["GET", RECURRING_PATH],
    ]);
  });

  it("surfaces a 404 as an ApiError carrying the backend detail", async () => {
    serveRecurring(() => HttpResponse.json({ detail: "engine not found" }, { status: 404 }));
    const outcome = await api.getRecurringStatus(SLUG).then(
      () => null,
      (err: unknown) => err,
    );
    expect(outcome).toBeInstanceOf(ApiError);
    expect((outcome as ApiError).detail).toBe("engine not found");
    expect((outcome as ApiError).status).toBe(404);
  });

  it.each(["", "owner-only", "a b/repo", "owner/re po", "owner/repo/extra"])(
    "throws before any request for the malformed key %j",
    async (key) => {
      serveRecurring(() => HttpResponse.json(buildWireStatus([])));
      await expect(api.getRecurringStatus(key)).rejects.toThrow(/^invalid engine_(owner|repo): /);
      expect(arrivals).toEqual([]);
    },
  );

  it("never attaches a stored control token to the read", async () => {
    writeControlToken("tok-recurring");
    serveRecurring(() => HttpResponse.json(buildWireStatus([])));
    await api.getRecurringStatus(SLUG);
    expect(arrivals[0]?.headers.has(CONTROL_TOKEN_HEADER)).toBe(false);
  });
});

describe("recurring-status generated types", () => {
  it("expose the cadence, overdue and run states as non-nullable generated unions", () => {
    expectTypeOf<RecurringCadenceKind>().toEqualTypeOf<
      "interval_seconds" | "cron" | "manual" | "rides_on"
    >();
    expectTypeOf<RecurringOverdueState>().toEqualTypeOf<"on_time" | "overdue" | "unknown">();
    expectTypeOf<RecurringRunState>().toEqualTypeOf<
      | "succeeded"
      | "failed"
      | "attention"
      | "cancelled"
      | "in_progress"
      | "queued"
      | "never_ran"
      | "unknown"
    >();
  });

  it("expose the process family and duration state as non-nullable generated unions", () => {
    expectTypeOf<RecurringProcessFamily>().toEqualTypeOf<
      "scheduled_ci" | "dependency_update" | "system_sweep" | "backup"
    >();
    expectTypeOf<RecurringDurationState>().toEqualTypeOf<"measured" | "not_applicable" | "unknown">();
    expectTypeOf<null | undefined>().not.toExtend<RecurringRunState>();
  });
});
