import { render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { HealthMetricWindows, RepoHealthRow, RepoHealthValue } from "../../../api";
import {
  emptyMetric,
  healthWindow,
  metricWindows,
  repoRow,
  scopeWindows,
} from "../../../test/healthWindows";
import RepoHealthTable from "./RepoHealthTable";

function value(v: number | null, sample = 0, lowData = false): RepoHealthValue {
  return { value: v, sample, low_data: lowData };
}

function steady(rate: number, sample = 40): HealthMetricWindows {
  return metricWindows(healthWindow(rate, sample), healthWindow(rate, sample), "same");
}

function makeRow(repo: string, overrides: Partial<RepoHealthRow> = {}): RepoHealthRow {
  return repoRow(repo, {
    outstanding_findings: value(2),
    in_flight: value(1),
    windows: scopeWindows({
      build_success: steady(0.8),
      outcome: steady(0.7),
      autonomous: steady(0.5),
      first_pass_approval: steady(0.9),
    }),
    ...overrides,
  });
}

function renderTable(props: Partial<ComponentProps<typeof RepoHealthTable>> = {}) {
  return render(
    <MemoryRouter>
      <RepoHealthTable
        rows={[]}
        computedAt={null}
        blockedCount={0}
        fleet={scopeWindows()}
        qualityMetrics={null}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe("RepoHealthTable — empty state", () => {
  it("renders an empty message and no table when there are no rows", () => {
    renderTable({ rows: [] });
    expect(screen.getByTestId("repo-health-table-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-health-table")).not.toBeInTheDocument();
  });
});

describe("RepoHealthTable — rows", () => {
  it("renders one row per repository, ordered worst-findings-first", () => {
    renderTable({
      rows: [
        makeRow("a/quiet", { outstanding_findings: value(1) }),
        makeRow("b/loud", { outstanding_findings: value(9) }),
      ],
    });
    const rows = screen.getAllByRole("row");
    const bodyRowText = rows.map((r) => r.textContent ?? "");
    const loudIndex = bodyRowText.findIndex((t) => t.includes("b/loud"));
    const quietIndex = bodyRowText.findIndex((t) => t.includes("a/quiet"));
    expect(loudIndex).toBeGreaterThan(-1);
    expect(quietIndex).toBeGreaterThan(loudIndex);
  });

  it("renders the repository-name cell as a keyboard-reachable link, not a row click target", () => {
    renderTable({ rows: [makeRow("acme/widget")] });
    const row = screen.getByTestId("repo-health-row-acme/widget");
    expect(row.tagName).toBe("TR");
    expect(row).not.toHaveAttribute("onclick");
    expect(row).not.toHaveAttribute("role", "button");
    const link = screen.getByTestId("repo-health-link-acme/widget");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/health?engine=acme%2Fwidget");
  });

  it("shows the 30-day value at full emphasis with its verdict symbol and no sample size when data is enough", () => {
    renderTable({
      rows: [
        makeRow("acme/widget", {
          windows: scopeWindows({
            build_success: metricWindows(healthWindow(0.9, 40), healthWindow(0.8, 40), "better"),
          }),
        }),
      ],
    });
    const cell = screen.getByTestId("repo-health-cell-build-acme/widget");
    expect(cell.textContent).toBe("80%");
    expect(cell.className).toContain("text-text-primary");
    expect(screen.getByTestId("repo-health-cell-build-acme/widget-symbol")).toHaveTextContent("▲");
    expect(screen.queryByTestId("repo-health-cell-build-acme/widget-sample")).not.toBeInTheDocument();
  });

  it.each([
    ["worse", "▼"],
    ["same", "≈"],
  ] as const)("shows the %s symbol beside a value", (verdict, symbol) => {
    renderTable({
      rows: [
        makeRow("acme/widget", {
          windows: scopeWindows({
            outcome: metricWindows(healthWindow(0.5, 40), healthWindow(0.5, 40), verdict),
          }),
        }),
      ],
    });
    expect(screen.getByTestId("repo-health-cell-outcome-acme/widget-symbol")).toHaveTextContent(symbol);
  });

  it("dims a low-data value, shows its sample size, and gives it no symbol", () => {
    renderTable({
      rows: [
        makeRow("acme/widget", {
          windows: scopeWindows({
            first_pass_approval: metricWindows(healthWindow(1, 1), healthWindow(1, 1)),
          }),
        }),
      ],
    });
    const cell = screen.getByTestId("repo-health-cell-firstpass-acme/widget");
    expect(cell.textContent).toBe("100%");
    expect(cell.className).toContain("text-text-muted");
    expect(screen.getByTestId("repo-health-cell-firstpass-acme/widget-sample").textContent).toBe("n=1");
    expect(screen.queryByTestId("repo-health-cell-firstpass-acme/widget-symbol")).not.toBeInTheDocument();
  });

  it("renders an em dash with no sample size when the 30-day window has no data", () => {
    renderTable({ rows: [makeRow("acme/widget", { windows: scopeWindows({ outcome: emptyMetric() }) })] });
    expect(screen.getByTestId("repo-health-cell-outcome-acme/widget").textContent).toBe("—");
    expect(screen.queryByTestId("repo-health-cell-outcome-acme/widget-sample")).not.toBeInTheDocument();
  });

  it("gives screen readers the full comparison sentence for each rate cell", () => {
    renderTable({
      rows: [
        makeRow("acme/widget", {
          windows: scopeWindows({
            autonomous: metricWindows(healthWindow(0.9, 40), healthWindow(0.6, 80), "better"),
          }),
        }),
      ],
    });
    const cell = screen.getByTestId("repo-health-cell-autonomous-acme/widget").closest("td");
    expect(cell).toHaveTextContent(
      "Autonomous: last 7 days 90%, n=40; last 30 days 60%, n=80; the last 7 days are better than the last 30 days.",
    );
  });

  it("keeps the findings and in-flight cells as they were", () => {
    renderTable({
      rows: [makeRow("acme/widget", { outstanding_findings: value(6, 2), in_flight: value(3, 3) })],
    });
    expect(screen.getByTestId("repo-health-cell-findings-acme/widget").textContent).toBe("6");
    expect(screen.getByTestId("repo-health-cell-inflight-acme/widget").textContent).toBe("3");
  });
});

describe("RepoHealthTable — findings and in-flight cells", () => {
  it("renders a dash for a null count and dims a low-data count with its sample size", () => {
    renderTable({
      rows: [
        makeRow("acme/widget", {
          outstanding_findings: value(null),
          in_flight: value(2, 2, true),
        }),
      ],
    });
    expect(screen.getByTestId("repo-health-cell-findings-acme/widget").textContent).toBe("—");
    const inFlight = screen.getByTestId("repo-health-cell-inflight-acme/widget");
    expect(inFlight.className).toContain("text-text-muted");
    expect(screen.getByTestId("repo-health-cell-inflight-acme/widget-sample").textContent).toBe("n=2");
  });
});

describe("RepoHealthTable — scroll region", () => {
  it("keeps its screen-reader-only sentences inside the horizontally scrolling region", () => {
    renderTable({ rows: [makeRow("a/repo")] });
    const region = screen.getByLabelText("Repository health table, scroll horizontally for more columns");
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveClass("relative", "overflow-x-auto");
    expect(region).toContainElement(screen.getByTestId("repo-health-table"));
  });
});

describe("RepoHealthTable — legend", () => {
  it("renders the persistent legend below the table when the table is shown", () => {
    renderTable({ rows: [makeRow("a/repo")] });
    const legend = screen.getByTestId("repo-health-legend");
    const table = screen.getByTestId("repo-health-table");
    expect(table.compareDocumentPosition(legend) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("omits the legend while there is no table", () => {
    renderTable({ rows: [] });
    expect(screen.queryByTestId("repo-health-legend")).not.toBeInTheDocument();
  });
});

describe("RepoHealthTable — fleet footer row", () => {
  it("renders the fleet 30-day figures with verdict symbols, independent of the per-repository rows", () => {
    renderTable({
      rows: [makeRow("a/repo", { outstanding_findings: value(100) })],
      blockedCount: 3,
      fleet: scopeWindows({
        build_success: metricWindows(healthWindow(0.7, 50), healthWindow(0.6, 200), "better"),
        outcome: metricWindows(healthWindow(0.3, 50), healthWindow(0.42, 200), "worse"),
        autonomous: metricWindows(healthWindow(0.33, 50), healthWindow(0.33, 200), "same"),
        first_pass_approval: metricWindows(healthWindow(0.5, 4), healthWindow(0.55, 6)),
      }),
      qualityMetrics: {
        build_success_rate: 0.99,
        in_flight_build_count: 7,
        first_pass_approval_rate: 0.99,
        mean_fix_cycles_to_merge: null,
        sample_size: 50,
        computed_at: "2026-06-01T00:00:00Z",
        format_error_rate: null,
        format_error_sample: 0,
        format_error_low_data: true,
        recovery_rate: null,
        recovery_sample: 0,
        recovery_low_data: true,
        unproven_clause_count: 0,
        rescue_success_rate: null,
        rescue_success_rate_sample: 0,
        rescue_success_rate_low_data: true,
        rescue_load_rate: null,
        rescue_load_rate_sample: 0,
        rescue_load_rate_low_data: true,
      },
    });
    const footer = screen.getByTestId("repo-health-footer-row");
    expect(footer).toHaveTextContent("All repositories");
    expect(screen.getByTestId("repo-health-footer-findings").textContent).toBe("3 (unweighted)");
    expect(screen.getByTestId("repo-health-footer-build-value").textContent).toBe("60%");
    expect(screen.getByTestId("repo-health-footer-build-value-symbol").textContent).toBe("▲");
    expect(screen.getByTestId("repo-health-footer-outcome-value").textContent).toBe("42%");
    expect(screen.getByTestId("repo-health-footer-outcome-value-symbol").textContent).toBe("▼");
    expect(screen.getByTestId("repo-health-footer-autonomous-value").textContent).toBe("33%");
    expect(screen.getByTestId("repo-health-footer-autonomous-value-symbol").textContent).toBe("≈");
    expect(screen.getByTestId("repo-health-footer-inflight").textContent).toBe("7");
    const firstPass = screen.getByTestId("repo-health-footer-firstpass");
    expect(within(firstPass).getByTestId("repo-health-footer-firstpass-value").textContent).toBe("55%");
    expect(within(firstPass).getByTestId("repo-health-footer-firstpass-value-sample").textContent).toBe("n=6");
  });

  it("renders dashes for absent fleet figures", () => {
    renderTable({ rows: [makeRow("a/repo")], qualityMetrics: null });
    expect(screen.getByTestId("repo-health-footer-build-value").textContent).toBe("—");
    expect(screen.getByTestId("repo-health-footer-outcome-value").textContent).toBe("—");
    expect(screen.getByTestId("repo-health-footer-inflight").textContent).toBe("—");
  });

  it("omits the footer when no fleet windows have loaded", () => {
    renderTable({ rows: [makeRow("a/repo")], fleet: null });
    expect(screen.queryByTestId("repo-health-footer-row")).not.toBeInTheDocument();
  });
});

describe("RepoHealthTable — freshness", () => {
  it("renders a freshness indicator when computedAt is present", () => {
    renderTable({ rows: [makeRow("a/repo")], computedAt: "2026-06-01T00:00:00Z" });
    expect(screen.getByTestId("repo-health-freshness")).toBeInTheDocument();
  });

  it("omits the freshness indicator when computedAt is null", () => {
    renderTable({ rows: [makeRow("a/repo")], computedAt: null });
    expect(screen.queryByTestId("repo-health-freshness")).not.toBeInTheDocument();
  });
});

describe("RepoHealthTable — daily digest signal", () => {
  it("renders the digest signal section with a not-measured-yet state by default", () => {
    renderTable({ rows: [makeRow("a/repo")] });
    expect(screen.getByTestId("digest-signal-section")).toBeInTheDocument();
    expect(screen.getByTestId("metric-digest-recovery")).toHaveTextContent("not measured yet");
    expect(screen.getByTestId("aggregate-chip-digest-sweep")).toHaveTextContent("Unknown");
  });

  it("passes the recovery snapshot and overdue flag through to the section", () => {
    renderTable({
      rows: [makeRow("a/repo")],
      digestRecovery: { current_count: 7, previous_count: 3, direction: "up" },
      digestOverdue: true,
    });
    expect(screen.getByTestId("metric-digest-recovery")).toHaveTextContent("7");
    expect(screen.getByTestId("aggregate-chip-digest-sweep")).toHaveTextContent("Overdue");
  });

  it("passes the residue list through to the section", () => {
    renderTable({
      rows: [makeRow("a/repo")],
      digestResidue: [{ fingerprint: "fp-1", title: "Unclassified anomaly" }],
    });
    expect(screen.getByTestId("metric-digest-residue")).toHaveTextContent("1");
  });

  it("renders the digest signal section even in the empty-rows state", () => {
    renderTable({ rows: [] });
    expect(screen.getByTestId("digest-signal-section")).toBeInTheDocument();
  });
});

describe("RepoHealthTable — loading and error states", () => {
  it("shows a loading message, not the empty message or the table, while rows have not loaded yet", () => {
    renderTable({ rows: [], loaded: false });
    expect(screen.getByTestId("repo-health-table-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-health-table-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-health-table")).not.toBeInTheDocument();
  });

  it("shows an error message, not the empty message, once loading finished with a failure", () => {
    renderTable({ rows: [], loaded: true, error: true });
    expect(screen.getByTestId("repo-health-table-error")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-health-table-empty")).not.toBeInTheDocument();
  });

  it("prefers the loading message over the error message while still loading", () => {
    renderTable({ rows: [], loaded: false, error: true });
    expect(screen.getByTestId("repo-health-table-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-health-table-error")).not.toBeInTheDocument();
  });
});
