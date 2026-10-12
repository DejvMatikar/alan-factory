import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { RecurringProcess, RecurringRunState, RecurringStatus } from "../../../api";
import { ServerEventsContext, type ServerEventsValue } from "../../../api/ServerEventsProvider";
import {
  activateDisclosure,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { server } from "../../../test/server";
import RecurringProcessesPanel from "./RecurringProcessesPanel";

function mockStatus(slug: string, status: RecurringStatus): void {
  server.use(http.get(`/api/engines/${slug}/recurring-status`, () => HttpResponse.json(status)));
}

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

function status(overrides: Partial<RecurringStatus> = {}): RecurringStatus {
  return {
    slug: "acme/widget",
    is_default: false,
    processes: [process()],
    service_scope_note: null,
    backup_coverage: {
      provenance: { status: "fresh", detail: "most recent backup_receipt.json" },
      included_in_last_snapshot: true,
      changed_since_count: 0,
      excluded_caveat: "per-repository engine configuration is structurally excluded",
    },
    ...overrides,
  };
}

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="*" element={<RecurringProcessesPanel engineKey="acme/widget" />} />
      </Routes>
    </MemoryRouter>,
  );
}

function eventsValue(engines: number): ServerEventsValue {
  return {
    counters: { workspace_list: 0, health: 0, engines },
    connected: true,
  };
}

function renderPanelWithEvents(engines: number) {
  return render(
    <ServerEventsContext.Provider value={eventsValue(engines)}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="*" element={<RecurringProcessesPanel engineKey="acme/widget" />} />
        </Routes>
      </MemoryRouter>
    </ServerEventsContext.Provider>,
  );
}

describe("RecurringProcessesPanel", () => {
  it("renders a loading state before the fetch resolves", () => {
    server.use(
      http.get("/api/engines/acme/widget/recurring-status", () => new Promise(() => undefined)),
    );
    renderPanel();
    expect(screen.getByTestId("recurring-status-loading-acme/widget")).toBeInTheDocument();
  });

  it("renders an unavailable message, never a crash, when the fetch itself fails", async () => {
    server.use(
      http.get("/api/engines/acme/widget/recurring-status", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderPanel();
    expect(await screen.findByTestId("recurring-status-error-acme/widget")).toBeInTheDocument();
  });

  it("renders a closed disclosure with the deliberate zero-processes sentence as its summary, not an error, hiding the footnotes until expanded", async () => {
    mockStatus("acme/widget", status({ processes: [] }));
    renderPanel();
    const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    expect(disclosure).not.toHaveAttribute("open");
    expect(screen.getByTestId("recurring-empty-acme/widget")).toHaveTextContent(
      "No recurring processes are tracked for this repository yet.",
    );
    expect(screen.getByTestId("recurring-backup-coverage-acme/widget")).not.toBeVisible();
  });

  it("collapses the disclosure by default with a summary folding failed and attention rows plus failing names into one figure", async () => {
    mockStatus(
      "acme/widget",
      status({
        processes: [
          process({ process_id: "a", overdue: { state: "overdue", detail: "late" } }),
          process({ process_id: "b", last_run: { ...process().last_run, state: "failed" } }),
        ],
      }),
    );
    renderPanel();
    const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    expect(disclosure).not.toHaveAttribute("open");
    expect(
      within(disclosure).getByText(
        "Recurring processes — 2 processes · 1 overdue · 1 need attention · Failing: CI workflow",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("recurring-row-a")).not.toBeVisible();
  });

  it("renders the h2 heading and the merged summary row on their own separate lines, left-aligned in both states", async () => {
    mockStatus("acme/widget", status());
    renderPanel();
    const panel = await screen.findByTestId("recurring-processes-panel-acme/widget");
    const heading = within(panel).getByRole("heading", { level: 2, name: "Recurring Processes" });
    const disclosure = within(panel).getByTestId("recurring-processes-disclosure-acme/widget");
    expect(heading.compareDocumentPosition(disclosure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(heading.parentElement).not.toBe(disclosure.parentElement);
  });

  it("expands via mouse click and reveals every row field as text, not colour alone", async () => {
    const user = userEvent.setup();
    mockStatus("acme/widget", status());
    renderPanel();
    const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    const summaryText = await screen.findByText(/Recurring processes/);
    await user.click(summaryText);
    await waitFor(() => expect(disclosure).toHaveAttribute("open"));

    const row = screen.getByTestId("recurring-row-scheduled_ci:1");
    expect(within(row).getByText("CI workflow")).toBeInTheDocument();
    expect(within(row).getByText("On time — last ran 60s ago; expected every 1800s")).toBeInTheDocument();
    expect(within(row).getByText("Every 30 minutes")).toBeInTheDocument();
    expect(within(row).getAllByText("Succeeded").length).toBeGreaterThanOrEqual(2);
    expect(within(row).getByText("2m")).toBeInTheDocument();
  });

  it("opens and closes the disclosure by click, Enter and Space, and ignores non-activation keys", async () => {
    mockStatus("acme/widget", status());
    renderPanel();
    const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    await expectDisclosureState(disclosure, false);
    await expectTogglesOnEveryActivation(disclosure);
  });

  it("renders not_applicable and unknown durations as plain words with a reason, never a fabricated number", async () => {
    mockStatus(
      "acme/widget",
      status({
        processes: [
          process({
            process_id: "dependency_update:npm",
            family: "dependency_update",
            duration_state: "not_applicable",
            duration_seconds: null,
          }),
          process({
            process_id: "backup_job",
            family: "backup",
            duration_state: "unknown",
            duration_seconds: null,
            last_run: {
              provenance: { status: "unknown", detail: "no backup receipt found yet" },
              at: null,
              state: "unknown",
              outcome: null,
              url: null,
            },
          }),
        ],
      }),
    );
    renderPanel();
    await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    fireEvent.click(screen.getByText(/Recurring processes/));

    expect(
      within(screen.getByTestId("recurring-row-dependency_update:npm")).getByText(
        "Not applicable — this kind of process exposes no run duration to measure",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("recurring-row-backup_job")).getByText(
        "Unknown — no backup receipt found yet",
      ),
    ).toBeInTheDocument();
  });

  it("renders a sub-step nested under its parent row, e.g. the daily digest under the triage sweep", async () => {
    mockStatus(
      "acme/widget",
      status({
        processes: [
          process({
            process_id: "triage_sweep",
            family: "system_sweep",
            label: "Failure triage sweep",
            sub_steps: [
              process({
                process_id: "daily_digest",
                family: "system_sweep",
                label: "Daily digest",
                cadence: {
                  kind: "rides_on",
                  interval_seconds: null,
                  cron_expression: null,
                  rides_on: "triage_sweep",
                  detail: "generated once a day",
                },
                duration_state: "not_applicable",
                duration_seconds: null,
              }),
            ],
          }),
        ],
      }),
    );
    renderPanel();
    const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    expect(within(disclosure).getByText("Recurring processes — 2 processes")).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Recurring processes/));

    const parentRow = screen.getByTestId("recurring-row-triage_sweep");
    const subRow = within(parentRow).getByTestId("recurring-row-daily_digest");
    expect(within(subRow).getByText("Daily digest")).toBeInTheDocument();
    expect(within(subRow).getByText("Rides on triage_sweep — generated once a day")).toBeInTheDocument();
  });

  it("hides the service_scope_note and backup coverage footnotes while the disclosure is collapsed", async () => {
    mockStatus(
      "acme/widget",
      status({
        service_scope_note:
          "the failure-triage sweep, daily digest, and state backup are system-wide processes",
      }),
    );
    renderPanel();
    await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    expect(screen.queryByTestId("recurring-service-scope-note-acme/widget")).not.toBeVisible();
    expect(screen.queryByTestId("recurring-backup-coverage-acme/widget")).not.toBeVisible();
  });

  it("renders the service_scope_note alongside the backup entry once the disclosure is expanded, for a non-default repository section", async () => {
    mockStatus(
      "acme/widget",
      status({
        service_scope_note:
          "the failure-triage sweep, daily digest, and state backup are system-wide processes",
      }),
    );
    renderPanel();
    await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    fireEvent.click(screen.getByText(/Recurring processes/));
    expect(
      await screen.findByTestId("recurring-service-scope-note-acme/widget"),
    ).toHaveTextContent("system-wide processes");
  });

  it("renders no service_scope_note element when the field is null, even expanded", async () => {
    mockStatus("acme/widget", status({ service_scope_note: null }));
    renderPanel();
    await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    fireEvent.click(screen.getByText(/Recurring processes/));
    expect(screen.queryByTestId("recurring-service-scope-note-acme/widget")).not.toBeInTheDocument();
  });

  it("opens and closes the zero-processes disclosure by click, Enter and Space, and ignores non-activation keys", async () => {
    mockStatus("acme/widget", status({ processes: [] }));
    renderPanel();
    const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    await expectDisclosureState(disclosure, false);
    await expectTogglesOnEveryActivation(disclosure);
  });

  it("keeps the disclosure open when the same panel refetches and stays mounted", async () => {
    mockStatus("acme/widget", status());
    const view = renderPanelWithEvents(0);
    const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    activateDisclosure(disclosure, "Enter");
    await expectDisclosureState(disclosure, true);

    view.rerender(
      <ServerEventsContext.Provider value={eventsValue(1)}>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route path="*" element={<RecurringProcessesPanel engineKey="acme/widget" />} />
          </Routes>
        </MemoryRouter>
      </ServerEventsContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("recurring-processes-panel-acme/widget")).toHaveAttribute(
        "aria-busy",
        "false",
      );
    });
    await expectDisclosureState(
      screen.getByTestId("recurring-processes-disclosure-acme/widget"),
      true,
    );
  });

  it("reveals the backup coverage line, gated behind the zero-processes disclosure, once it is expanded", async () => {
    mockStatus(
      "acme/widget",
      status({
        processes: [],
        backup_coverage: {
          provenance: { status: "fresh", detail: "most recent backup_receipt.json" },
          included_in_last_snapshot: true,
          changed_since_count: 2,
          excluded_caveat: "per-repository engine configuration is structurally excluded",
        },
      }),
    );
    renderPanel();
    const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    fireEvent.click(screen.getByText(/No recurring processes/));
    expect(disclosure).toHaveAttribute("open");
    expect(await screen.findByTestId("recurring-backup-coverage-acme/widget")).toHaveTextContent(
      "Covered by the most recent backup snapshot · 2 changes since.",
    );
  });

  it("renders the explicit zero-changes clause once expanded, when no workspace changed since the snapshot", async () => {
    mockStatus("acme/widget", status());
    renderPanel();
    await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    fireEvent.click(screen.getByText(/Recurring processes/));
    expect(await screen.findByTestId("recurring-backup-coverage-acme/widget")).toHaveTextContent(
      "Covered by the most recent backup snapshot · 0 changes since.",
    );
  });

  it("renders the unknown-changes clause with no provenance detail once expanded, when the change count could not be computed", async () => {
    mockStatus(
      "acme/widget",
      status({
        backup_coverage: {
          provenance: { status: "fresh", detail: "most recent backup_receipt.json" },
          included_in_last_snapshot: true,
          changed_since_count: null,
          excluded_caveat: "per-repository engine configuration is structurally excluded",
        },
      }),
    );
    renderPanel();
    await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    fireEvent.click(screen.getByText(/Recurring processes/));
    const coverageLine = await screen.findByTestId("recurring-backup-coverage-acme/widget");
    expect(coverageLine).toHaveTextContent(
      "Covered by the most recent backup snapshot · changes since unknown.",
    );
    expect(coverageLine).not.toHaveTextContent("most recent backup_receipt.json");
  });

  it("labels the Recurring Processes heading", async () => {
    mockStatus("acme/widget", status());
    renderPanel();
    await screen.findByTestId("recurring-processes-disclosure-acme/widget");
    expect(screen.getByRole("heading", { level: 2, name: "Recurring Processes" })).toBeInTheDocument();
  });

  describe("failures named in the collapsed summary, without expanding anything", () => {
    it("appends no Failing clause when nothing failed", async () => {
      mockStatus("acme/widget", status());
      renderPanel();
      const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      expect(within(disclosure).queryByText(/Failing:/)).not.toBeInTheDocument();
    });

    it("names a failing process as plain text in the always-visible collapsed summary, not a link, without opening the disclosure", async () => {
      mockStatus(
        "acme/widget",
        status({
          processes: [
            process({ process_id: "a", label: "Nightly build", last_run: { ...process().last_run, state: "failed" } }),
          ],
        }),
      );
      renderPanel();
      const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      expect(disclosure).not.toHaveAttribute("open");
      const summary = disclosure.querySelector("summary");
      expect(summary).toHaveTextContent("Failing: Nightly build");
      expect(within(disclosure).getByRole("link", { name: /Nightly build/ })).not.toBeVisible();
    });

    it("names the same failing process as a real link once the disclosure is expanded", async () => {
      mockStatus(
        "acme/widget",
        status({
          processes: [
            process({ process_id: "a", label: "Nightly build", last_run: { ...process().last_run, state: "failed" } }),
          ],
        }),
      );
      renderPanel();
      const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      fireEvent.click(screen.getByText(/Recurring processes/));
      expect(disclosure).toHaveAttribute("open");
      const row = screen.getByTestId("recurring-row-a");
      expect(within(row).getByRole("link", { name: /Nightly build/ })).toBeInTheDocument();
    });

    it("names at most three failing processes then appends a +N more count in the summary text", async () => {
      mockStatus(
        "acme/widget",
        status({
          processes: [1, 2, 3, 4, 5].map((n) =>
            process({
              process_id: `p${n}`,
              label: `Job ${n}`,
              last_run: { ...process().last_run, state: "failed" },
            }),
          ),
        }),
      );
      renderPanel();
      const disclosure = await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      const summary = disclosure.querySelector("summary");
      expect(summary).toHaveTextContent("Failing: Job 1, Job 2, Job 3 +2 more");
    });
  });

  describe("run-state chip tone and label per state", () => {
    const CASES: readonly [RecurringRunState, string][] = [
      ["succeeded", "Succeeded"],
      ["failed", "Failed"],
      ["attention", "Needs attention"],
      ["cancelled", "Cancelled"],
      ["in_progress", "Running"],
      ["queued", "Queued"],
      ["never_ran", "Never run"],
      ["unknown", "Unknown"],
    ];

    it.each(CASES)("renders the %s chip with label %s", async (state, label) => {
      mockStatus(
        "acme/widget",
        status({ processes: [process({ last_run: { ...process().last_run, state, url: null } })] }),
      );
      renderPanel();
      await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      fireEvent.click(screen.getByText(/Recurring processes/));
      const chip = screen.getByTestId("aggregate-chip-recurring-outcome-scheduled_ci:1");
      expect(chip).toHaveTextContent(label);
    });
  });

  describe("navigation: every row that has somewhere to go, goes there", () => {
    it("gives an external run link an accessible name identifying its own process", async () => {
      mockStatus(
        "acme/widget",
        status({
          processes: [
            process({ process_id: "a", label: "Nightly build" }),
            process({
              process_id: "b",
              label: "Weekly audit",
              last_run: { ...process().last_run, url: "https://github.com/acme/widget/actions/runs/2" },
            }),
          ],
        }),
      );
      renderPanel();
      await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      fireEvent.click(screen.getByText(/Recurring processes/));
      const linkA = within(screen.getByTestId("recurring-row-a")).getByRole("link");
      const linkB = within(screen.getByTestId("recurring-row-b")).getByRole("link");
      expect(linkA.getAttribute("aria-label")).not.toBe(linkB.getAttribute("aria-label"));
      expect(linkA).toHaveAccessibleName(/Nightly build/);
      expect(linkB).toHaveAccessibleName(/Weekly audit/);
      expect(linkA).toHaveAttribute("target", "_blank");
      expect(linkA).toHaveAttribute("rel", "noopener noreferrer");
    });

    it("renders no link and no href for a forge row with a null url, only plain text", async () => {
      mockStatus(
        "acme/widget",
        status({ processes: [process({ last_run: { ...process().last_run, url: null } })] }),
      );
      renderPanel();
      await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      fireEvent.click(screen.getByText(/Recurring processes/));
      const row = screen.getByTestId("recurring-row-scheduled_ci:1");
      expect(within(row).queryByRole("link")).not.toBeInTheDocument();
      expect(within(row).getByText("CI workflow")).toBeInTheDocument();
    });

    it("links the triage sweep in-app to /health", async () => {
      mockStatus(
        "acme/widget",
        status({
          processes: [process({ process_id: "triage_sweep", family: "system_sweep", label: "Failure triage sweep" })],
        }),
      );
      renderPanel();
      await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      fireEvent.click(screen.getByText(/Recurring processes/));
      const link = within(screen.getByTestId("recurring-row-triage_sweep")).getByRole("link");
      expect(link).toHaveAttribute("href", "/health");
      expect(link).not.toHaveAttribute("target");
    });

    it("links the dependency sweep to this repo's engine configuration", async () => {
      mockStatus(
        "acme/widget",
        status({
          processes: [
            process({
              process_id: "dependency_sweep",
              family: "system_sweep",
              label: "Alan's dependency sweep",
            }),
          ],
        }),
      );
      renderPanel();
      await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      fireEvent.click(screen.getByText(/Recurring processes/));
      const link = within(screen.getByTestId("recurring-row-dependency_sweep")).getByRole("link");
      expect(link).toHaveAttribute("href", "/repos/acme/widget/config");
      expect(link).toHaveAccessibleName(/configuration/);
    });

    it("renders the state backup row with no link but a visible, plain-text reason", async () => {
      mockStatus(
        "acme/widget",
        status({
          processes: [
            process({
              process_id: "backup_job",
              family: "backup",
              label: "State backup",
              last_run: { ...process().last_run, url: null },
            }),
          ],
        }),
      );
      renderPanel();
      await screen.findByTestId("recurring-processes-disclosure-acme/widget");
      fireEvent.click(screen.getByText(/Recurring processes/));
      const row = screen.getByTestId("recurring-row-backup_job");
      expect(within(row).queryByRole("link")).not.toBeInTheDocument();
      expect(within(row).getByText(/No viewing surface in the product/)).toBeInTheDocument();
    });
  });

  describe("engines-bucket refetch stability", () => {
    it("stays mounted with an aria-busy refreshing cue instead of collapsing to the loading placeholder when the engines bucket bumps", async () => {
      let callCount = 0;
      let resolveSecond!: () => void;
      const secondGate = new Promise<void>((resolve) => {
        resolveSecond = resolve;
      });
      server.use(
        http.get("/api/engines/acme/widget/recurring-status", async () => {
          callCount += 1;
          if (callCount === 2) await secondGate;
          return HttpResponse.json(status());
        }),
      );
      const view = renderPanelWithEvents(0);
      await screen.findByTestId("recurring-processes-panel-acme/widget");

      view.rerender(
        <ServerEventsContext.Provider value={eventsValue(1)}>
          <MemoryRouter initialEntries={["/"]}>
            <Routes>
              <Route path="*" element={<RecurringProcessesPanel engineKey="acme/widget" />} />
            </Routes>
          </MemoryRouter>
        </ServerEventsContext.Provider>,
      );

      await waitFor(() => {
        expect(screen.getByTestId("recurring-processes-panel-acme/widget")).toHaveAttribute(
          "aria-busy",
          "true",
        );
      });
      expect(screen.getByTestId("recurring-status-refreshing-acme/widget")).toBeInTheDocument();
      expect(
        screen.queryByTestId("recurring-status-loading-acme/widget"),
      ).not.toBeInTheDocument();

      resolveSecond();
      await waitFor(() => {
        expect(screen.getByTestId("recurring-processes-panel-acme/widget")).toHaveAttribute(
          "aria-busy",
          "false",
        );
      });
    });

    it("keeps the retained panel and shows an inline muted refresh-failed line, not the unavailable message, when only the refetch fails", async () => {
      let callCount = 0;
      server.use(
        http.get("/api/engines/acme/widget/recurring-status", () => {
          callCount += 1;
          if (callCount === 1) return HttpResponse.json(status());
          return HttpResponse.json({ detail: "boom" }, { status: 500 });
        }),
      );
      const view = renderPanelWithEvents(0);
      await screen.findByTestId("recurring-processes-panel-acme/widget");

      view.rerender(
        <ServerEventsContext.Provider value={eventsValue(1)}>
          <MemoryRouter initialEntries={["/"]}>
            <Routes>
              <Route path="*" element={<RecurringProcessesPanel engineKey="acme/widget" />} />
            </Routes>
          </MemoryRouter>
        </ServerEventsContext.Provider>,
      );

      const refreshErrorLine = await screen.findByTestId(
        "recurring-status-refresh-error-acme/widget",
      );
      expect(refreshErrorLine).toHaveTextContent("500");
      expect(screen.getByTestId("recurring-processes-panel-acme/widget")).toBeInTheDocument();
      expect(
        screen.queryByTestId("recurring-status-error-acme/widget"),
      ).not.toBeInTheDocument();
    });
  });
});
