import { render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type {
  RecurringProcess,
  RecurringStatus,
  RepoStatus,
  RepoStatusProvenance,
  RepoStatusSizeStat,
  UnblockScopedActivity,
} from "../../../api";
import { ServerEventsContext, type ServerEventsValue } from "../../../api/ServerEventsProvider";
import { server } from "../../../test/server";
import RepoStatusStrip from "./RepoStatusStrip";

const FRESH: RepoStatusProvenance = { status: "fresh", detail: "" };

function unknown(detail = ""): RepoStatusProvenance {
  return { status: "unknown", detail };
}

function mockStatus(slug: string, status: RepoStatus): void {
  server.use(http.get(`/api/engines/${slug}/status`, () => HttpResponse.json(status)));
}

function mockRecurringStatus(slug: string, status: RecurringStatus): void {
  server.use(http.get(`/api/engines/${slug}/recurring-status`, () => HttpResponse.json(status)));
}

function unblockerActivity(overrides: Partial<UnblockScopedActivity> = {}): UnblockScopedActivity {
  return {
    scope: "repo",
    repo: "acme/widget",
    window_days: 30,
    events_total: 0,
    workspaces_touched: 0,
    waited_workspaces: 0,
    diagnosed_workspaces: 0,
    diagnosable_workspaces: 0,
    still_stuck_workspaces: 0,
    gave_up_workspaces: 0,
    aggregates: [],
    fleet_ever_recorded: true,
    events: [],
    workspaces: [],
    ...overrides,
  };
}

function mockUnblockerActivity(payload: UnblockScopedActivity): void {
  server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.json(payload)));
}

beforeEach(() => {
  mockUnblockerActivity(unblockerActivity());
});

function recurringProcess(overrides: Partial<RecurringProcess> = {}): RecurringProcess {
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
      provenance: FRESH,
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

function recurringStatus(overrides: Partial<RecurringStatus> = {}): RecurringStatus {
  return {
    slug: "acme/widget",
    is_default: false,
    processes: [],
    service_scope_note: null,
    backup_coverage: {
      provenance: FRESH,
      included_in_last_snapshot: true,
      changed_since_count: 0,
      excluded_caveat: "per-repository engine configuration is structurally excluded",
    },
    ...overrides,
  };
}

const SAMPLE_BY_SIZE: RepoStatusSizeStat[] = [
  { size: "XS", median_active_seconds: 120, delivered_count: 4 },
  { size: "S", median_active_seconds: 600, delivered_count: 2 },
  { size: "M", median_active_seconds: null, delivered_count: 0 },
  { size: "L", median_active_seconds: null, delivered_count: 0 },
  { size: "XL", median_active_seconds: null, delivered_count: 0 },
];

function nonDefaultReadyStatus(): RepoStatus {
  return {
    slug: "acme/widget",
    is_default: false,
    main_ci: { provenance: FRESH, green_count: 3, red_count: 0, counted_over: "main_ci_workflow" },
    open_prs: { provenance: FRESH, total: 4, authored_by_alan: 2 },
    cost: { provenance: FRESH, total_cost_usd: 15, unknown_count: 0 },
    delivery_time: { provenance: FRESH, by_size: SAMPLE_BY_SIZE },
    last_deploy: {
      provenance: unknown("last deploy is only known for the default engine (deploys are alan-global)"),
      outcome: null,
      pr_number: null,
      at: null,
    },
    health: { provenance: FRESH, finding_count: 2, verdict: null },
  };
}

function eventsValue(engines: number): ServerEventsValue {
  return {
    counters: { workspace_list: 0, health: 0, engines },
    connected: true,
  };
}

function renderStrip(engineKey: string) {
  return render(
    <MemoryRouter>
      <RepoStatusStrip engineKey={engineKey} />
    </MemoryRouter>,
  );
}

function renderWithEvents(engines: number) {
  return render(
    <MemoryRouter>
      <ServerEventsContext.Provider value={eventsValue(engines)}>
        <RepoStatusStrip engineKey="acme/widget" />
      </ServerEventsContext.Provider>
    </MemoryRouter>,
  );
}

describe("RepoStatusStrip", () => {
  it("renders a loading state before the fetch resolves", () => {
    server.use(http.get("/api/engines/acme/widget/status", () => new Promise(() => undefined)));
    renderStrip("acme/widget");
    expect(screen.getByTestId("repo-status-loading-acme/widget")).toBeInTheDocument();
  });

  it("renders an unavailable message, never a crash, when the fetch itself fails", async () => {
    server.use(
      http.get("/api/engines/acme/widget/status", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderStrip("acme/widget");
    expect(await screen.findByTestId("repo-status-error-acme/widget")).toBeInTheDocument();
  });

  it("renders live CI, PR and cost figures for a non-default engine using its own data", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-main-ci")).toHaveTextContent(
      "3 green · 0 red · main CI workflow",
    );
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-open-prs")).toHaveTextContent(
      "4 open · 2 by alan",
    );
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-cost")).toHaveTextContent("$15.00");
  });

  it("renders a dash-equivalent unknown cost chip, distinct from a known-zero total", async () => {
    mockStatus("acme/widget", {
      ...nonDefaultReadyStatus(),
      cost: { provenance: FRESH, total_cost_usd: null, unknown_count: 0 },
    });
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-cost")).toHaveTextContent("Cost unknown");
  });

  it("appends an unknown-count suffix to the cost chip so the total reads as a floor", async () => {
    mockStatus("acme/widget", {
      ...nonDefaultReadyStatus(),
      cost: { provenance: FRESH, total_cost_usd: 15, unknown_count: 2 },
    });
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-cost")).toHaveTextContent(
      "$15.00 · 2 unknown",
    );
  });

  it("renders one delivery-time chip per size, with an explicit 'no data' state for empty buckets", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-delivery-XS")).toHaveTextContent(
      "XS · 2m · n=4",
    );
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-delivery-S")).toHaveTextContent(
      "S · 10m · n=2",
    );
    const emptyChip = within(strip).getByTestId("aggregate-chip-acme/widget-delivery-M");
    expect(emptyChip).toHaveTextContent("M · no data");
    expect(emptyChip).not.toHaveTextContent("0s");
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-delivery-L")).toHaveTextContent("no data");
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-delivery-XL")).toHaveTextContent("no data");
  });

  it("shows an unknown, never-red deploy chip on a non-default strip", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    const deployChip = within(strip).getByTestId("aggregate-chip-acme/widget-last-deploy");
    expect(deployChip).toHaveTextContent("unknown");
    expect(deployChip.className).not.toContain("status-danger");
  });

  it("renders no health status group — the contradictory fleet-wide verdict is retired from this strip", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    expect(within(strip).queryByTestId("aggregate-chip-acme/widget-health")).not.toBeInTheDocument();
    expect(within(strip).queryByText("Health")).not.toBeInTheDocument();
  });

  it("shows the default engine's deploy outcome, and reds only confirmed act-now states", async () => {
    const status: RepoStatus = {
      slug: "DejvMatikar/alan",
      is_default: true,
      main_ci: { provenance: FRESH, green_count: 1, red_count: 2, counted_over: "all_checks" },
      open_prs: { provenance: FRESH, total: 1, authored_by_alan: 1 },
      cost: { provenance: FRESH, total_cost_usd: 60, unknown_count: 0 },
      delivery_time: { provenance: FRESH, by_size: SAMPLE_BY_SIZE },
      last_deploy: { provenance: FRESH, outcome: "confirmed", pr_number: 900, at: "2026-07-28T00:00:00Z" },
      health: { provenance: FRESH, finding_count: 0, verdict: "healthy" },
    };
    mockStatus("DejvMatikar/alan", status);
    renderStrip("DejvMatikar/alan");
    const strip = await screen.findByTestId("repo-status-strip-DejvMatikar/alan");
    expect(within(strip).getByTestId("aggregate-chip-DejvMatikar/alan-last-deploy")).toHaveTextContent(
      "Deployed · PR #900",
    );
    const ciChip = within(strip).getByTestId("aggregate-chip-DejvMatikar/alan-main-ci");
    expect(ciChip.className).toContain("status-danger");
  });

  it("marks a stale main-CI value transitional, not red, on a single failed refresh", async () => {
    const status = nonDefaultReadyStatus();
    mockStatus("acme/widget", {
      ...status,
      main_ci: { provenance: { status: "stale", detail: "" }, green_count: 1, red_count: 3, counted_over: "all_checks" },
    });
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    const ciChip = within(strip).getByTestId("aggregate-chip-acme/widget-main-ci");
    expect(ciChip).toHaveTextContent("stale");
    expect(ciChip.className).toContain("status-warning");
    expect(ciChip.className).not.toContain("status-danger");
  });

  it("renders no cost or delivery-time caption", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    renderStrip("acme/widget");
    await screen.findByTestId("repo-status-strip-acme/widget");
    expect(screen.queryByTestId("repo-status-cost-caption-acme/widget")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-delivery-caption-acme/widget")).not.toBeInTheDocument();
  });

  it("renders each status group's label and chip on a single row, not stacked", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    renderStrip("acme/widget");
    const group = await screen.findByTestId("repo-status-group-acme/widget-main-ci");
    expect(group.className).toContain("flex");
    expect(group.className).not.toContain("flex-col");
    const chipsGroup = screen.getByTestId("repo-status-group-acme/widget-delivery");
    expect(chipsGroup.className).toContain("flex");
    expect(chipsGroup.className).not.toContain("flex-col");
  });

  it("renders every field across three static, non-scrolling rows with no tabIndex or aria-label scroll accommodation", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockRecurringStatus("acme/widget", recurringStatus());
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    const fieldset = strip.querySelector("fieldset");
    expect(fieldset).not.toBeNull();
    expect(fieldset).not.toHaveAttribute("tabindex");
    expect(fieldset).not.toHaveAttribute("aria-label");
    expect(fieldset?.className).not.toContain("overflow-x-auto");
    await within(strip).findByTestId("repo-status-group-acme/widget-recurring-total");
    const rows = fieldset?.querySelectorAll(":scope > div") ?? [];
    expect(rows).toHaveLength(3);
    expect(rows[0]).toContainElement(within(strip).getByTestId("aggregate-chip-acme/widget-main-ci"));
    expect(rows[0]).toContainElement(within(strip).getByTestId("aggregate-chip-acme/widget-last-deploy"));
    expect(rows[0]).toContainElement(within(strip).getByTestId("aggregate-chip-acme/widget-cost"));
    expect(rows[1]).toContainElement(within(strip).getByTestId("aggregate-chip-acme/widget-delivery-XS"));
    expect(rows[1]).not.toContainElement(within(strip).getByTestId("aggregate-chip-acme/widget-cost"));
    expect(rows[2]).toContainElement(within(strip).getByTestId("aggregate-chip-acme/widget-recurring-total"));
  });

  it("labels the Repository Status band and every field group", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    expect(within(strip).getByRole("heading", { level: 2, name: "Repository Status" })).toBeInTheDocument();
    for (const label of ["Main CI", "Open PRs", "Cost", "Delivery time", "Last deploy"]) {
      expect(within(strip).getByText(label)).toBeInTheDocument();
    }
  });

  it("stays mounted with an aria-busy refreshing cue instead of collapsing to the loading placeholder when the engines bucket bumps", async () => {
    let callCount = 0;
    let resolveSecond!: () => void;
    const secondGate = new Promise<void>((resolve) => {
      resolveSecond = resolve;
    });
    server.use(
      http.get("/api/engines/acme/widget/status", async () => {
        callCount += 1;
        if (callCount === 2) await secondGate;
        return HttpResponse.json(nonDefaultReadyStatus());
      }),
    );
    const view = renderWithEvents(0);
    await screen.findByTestId("repo-status-strip-acme/widget");

    view.rerender(
      <MemoryRouter>
        <ServerEventsContext.Provider value={eventsValue(1)}>
          <RepoStatusStrip engineKey="acme/widget" />
        </ServerEventsContext.Provider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("repo-status-strip-acme/widget")).toHaveAttribute(
        "aria-busy",
        "true",
      );
    });
    expect(screen.getByTestId("repo-status-refreshing-acme/widget")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-loading-acme/widget")).not.toBeInTheDocument();

    resolveSecond();
    await waitFor(() => {
      expect(screen.getByTestId("repo-status-strip-acme/widget")).toHaveAttribute(
        "aria-busy",
        "false",
      );
    });
  });

  it("keeps the retained status strip and shows an inline muted refresh-failed line, not the unavailable message, when only the refetch fails", async () => {
    let callCount = 0;
    server.use(
      http.get("/api/engines/acme/widget/status", () => {
        callCount += 1;
        if (callCount === 1) return HttpResponse.json(nonDefaultReadyStatus());
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const view = renderWithEvents(0);
    await screen.findByTestId("repo-status-strip-acme/widget");

    view.rerender(
      <MemoryRouter>
        <ServerEventsContext.Provider value={eventsValue(1)}>
          <RepoStatusStrip engineKey="acme/widget" />
        </ServerEventsContext.Provider>
      </MemoryRouter>,
    );

    const refreshErrorLine = await screen.findByTestId("repo-status-refresh-error-acme/widget");
    expect(refreshErrorLine).toHaveTextContent("500");
    expect(screen.getByTestId("repo-status-strip-acme/widget")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-error-acme/widget")).not.toBeInTheDocument();
  });
});

describe("RepoStatusStrip — row 3, recurring summary", () => {
  it("omits the Failed and Overdue chips, and shows no Failing indicator, when nothing has failed or gone overdue", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockRecurringStatus("acme/widget", recurringStatus({ processes: [] }));
    renderStrip("acme/widget");
    const total = await screen.findByTestId("repo-status-group-acme/widget-recurring-total");
    expect(within(total).getByTestId("aggregate-chip-acme/widget-recurring-total")).toHaveTextContent("0");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-succeeded")).toHaveTextContent("0");
    expect(screen.queryByTestId("repo-status-group-acme/widget-recurring-failed")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-group-acme/widget-recurring-overdue")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-group-acme/widget-recurring-failing")).not.toBeInTheDocument();
  });

  it("shows the Failed chip, omits Overdue, and names the failing process when only a failure occurred", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockRecurringStatus(
      "acme/widget",
      recurringStatus({
        processes: [
          recurringProcess({ process_id: "a", label: "Nightly build" }),
          recurringProcess({
            process_id: "b",
            label: "Deploy check",
            last_run: { ...recurringProcess().last_run, state: "failed" },
          }),
        ],
      }),
    );
    renderStrip("acme/widget");
    await screen.findByTestId("repo-status-group-acme/widget-recurring-total");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-total")).toHaveTextContent("2");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-succeeded")).toHaveTextContent("1");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-failed")).toHaveTextContent("1");
    expect(screen.queryByTestId("repo-status-group-acme/widget-recurring-overdue")).not.toBeInTheDocument();
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-failing")).toHaveTextContent("Deploy check");
  });

  it("shows the Overdue chip, omits Failed, and shows no Failing indicator when only a process has gone overdue", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockRecurringStatus(
      "acme/widget",
      recurringStatus({
        processes: [recurringProcess({ overdue: { state: "overdue", detail: "late" } })],
      }),
    );
    renderStrip("acme/widget");
    await screen.findByTestId("repo-status-group-acme/widget-recurring-total");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-total")).toHaveTextContent("1");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-succeeded")).toHaveTextContent("1");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-overdue")).toHaveTextContent("1");
    expect(screen.queryByTestId("repo-status-group-acme/widget-recurring-failed")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-group-acme/widget-recurring-failing")).not.toBeInTheDocument();
  });

  it("shows both Failed and Overdue chips together when both are non-zero", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockRecurringStatus(
      "acme/widget",
      recurringStatus({
        processes: [
          recurringProcess({ process_id: "a", overdue: { state: "overdue", detail: "late" } }),
          recurringProcess({
            process_id: "b",
            label: "Deploy check",
            last_run: { ...recurringProcess().last_run, state: "failed" },
          }),
        ],
      }),
    );
    renderStrip("acme/widget");
    await screen.findByTestId("repo-status-group-acme/widget-recurring-total");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-failed")).toHaveTextContent("1");
    expect(screen.getByTestId("aggregate-chip-acme/widget-recurring-overdue")).toHaveTextContent("1");
  });

  it("names at most two failing processes then appends a +N more count", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockRecurringStatus(
      "acme/widget",
      recurringStatus({
        processes: [1, 2, 3].map((n) =>
          recurringProcess({
            process_id: `p${String(n)}`,
            label: `Job ${String(n)}`,
            last_run: { ...recurringProcess().last_run, state: "failed" },
          }),
        ),
      }),
    );
    renderStrip("acme/widget");
    const failing = await screen.findByTestId("repo-status-group-acme/widget-recurring-failing");
    expect(failing).toHaveTextContent("Job 1, Job 2 +1 more");
  });

  it("links every recurring group to the repository's health page", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockRecurringStatus(
      "acme/widget",
      recurringStatus({
        processes: [recurringProcess({ last_run: { ...recurringProcess().last_run, state: "failed" } })],
      }),
    );
    renderStrip("acme/widget");
    const total = await screen.findByTestId("repo-status-group-acme/widget-recurring-total");
    expect(total).toHaveAttribute("href", "/health?engine=acme%2Fwidget");
    expect(screen.getByTestId("repo-status-group-acme/widget-recurring-failing")).toHaveAttribute(
      "href",
      "/health?engine=acme%2Fwidget",
    );
  });

  it("omits row 3 entirely while the recurring status is loading", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    server.use(http.get("/api/engines/acme/widget/recurring-status", () => new Promise(() => undefined)));
    renderStrip("acme/widget");
    await screen.findByTestId("repo-status-strip-acme/widget");
    expect(screen.queryByTestId("repo-status-group-acme/widget-recurring-total")).not.toBeInTheDocument();
  });

  it("omits row 3 entirely when the recurring status errors", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    server.use(
      http.get("/api/engines/acme/widget/recurring-status", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderStrip("acme/widget");
    await screen.findByTestId("repo-status-strip-acme/widget");
    await waitFor(() => {
      expect(screen.queryByTestId("repo-status-group-acme/widget-recurring-total")).not.toBeInTheDocument();
    });
  });
});

describe("RepoStatusStrip — row 4, unblocker summary", () => {
  it("shows events and workspaces touched as quiet chips, and no alarming chip, for a calm repository", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockUnblockerActivity(unblockerActivity({ events_total: 6, workspaces_touched: 4 }));
    renderStrip("acme/widget");
    const row = await screen.findByTestId("repo-status-unblocker-acme/widget");
    expect(within(row).getByTestId("aggregate-chip-acme/widget-unblocker-events")).toHaveTextContent("6");
    expect(within(row).getByTestId("aggregate-chip-acme/widget-unblocker-touched")).toHaveTextContent("4");
    expect(within(row).queryByTestId("repo-status-group-acme/widget-unblocker-stuck")).not.toBeInTheDocument();
    expect(within(row).queryByTestId("repo-status-group-acme/widget-unblocker-gave-up")).not.toBeInTheDocument();
  });

  it("adds red still-stuck and gave-up chips when workspaces are stuck or given up on", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockUnblockerActivity(
      unblockerActivity({
        events_total: 6,
        workspaces_touched: 4,
        still_stuck_workspaces: 2,
        gave_up_workspaces: 1,
      }),
    );
    renderStrip("acme/widget");
    await screen.findByTestId("repo-status-unblocker-acme/widget");
    const stuck = screen.getByTestId("aggregate-chip-acme/widget-unblocker-stuck");
    const gaveUp = screen.getByTestId("aggregate-chip-acme/widget-unblocker-gave-up");
    expect(stuck).toHaveTextContent("2");
    expect(gaveUp).toHaveTextContent("1");
    expect(stuck.className).toContain("status-danger");
    expect(gaveUp.className).toContain("status-danger");
  });

  it("renders no unblocker summary at all, not a row of zeros, when the window is quiet", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    await waitFor(() => {
      expect(strip.querySelectorAll("fieldset > div")).toHaveLength(3);
    });
    expect(screen.queryByTestId("repo-status-unblocker-acme/widget")).not.toBeInTheDocument();
  });

  it("links every unblocker chip to the repository's scoped health page", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockUnblockerActivity(
      unblockerActivity({ events_total: 6, workspaces_touched: 4, still_stuck_workspaces: 2, gave_up_workspaces: 1 }),
    );
    renderStrip("acme/widget");
    const row = await screen.findByTestId("repo-status-unblocker-acme/widget");
    const links = within(row).getAllByRole("link");
    expect(links).toHaveLength(4);
    for (const link of links) expect(link).toHaveAttribute("href", "/health?engine=acme%2Fwidget");
  });

  it("sits in its own fourth row after the recurring summary", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    mockRecurringStatus("acme/widget", recurringStatus());
    mockUnblockerActivity(unblockerActivity({ events_total: 1, workspaces_touched: 1 }));
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    await within(strip).findByTestId("repo-status-group-acme/widget-recurring-total");
    const unblocker = await within(strip).findByTestId("repo-status-unblocker-acme/widget");
    const rows = strip.querySelectorAll("fieldset > div");
    expect(rows).toHaveLength(4);
    expect(rows[3]).toBe(unblocker);
  });

  it("does not hold the strip back while the unblocker activity is still loading", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    server.use(http.get("/api/health/unblocker-activity", () => new Promise(() => undefined)));
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-main-ci")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-unblocker-acme/widget")).not.toBeInTheDocument();
  });

  it("keeps the strip intact and renders no unblocker summary when the unblocker request fails", async () => {
    mockStatus("acme/widget", nonDefaultReadyStatus());
    let requested = false;
    server.use(
      http.get("/api/health/unblocker-activity", () => {
        requested = true;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    renderStrip("acme/widget");
    const strip = await screen.findByTestId("repo-status-strip-acme/widget");
    await waitFor(() => {
      expect(requested).toBe(true);
    });
    expect(within(strip).getByTestId("aggregate-chip-acme/widget-cost")).toHaveTextContent("$15.00");
    expect(screen.queryByTestId("repo-status-unblocker-acme/widget")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-error-acme/widget")).not.toBeInTheDocument();
  });
});
