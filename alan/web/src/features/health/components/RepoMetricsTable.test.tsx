import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HealthScopeWindows } from "../../../api";
import {
  ZERO_FUNNEL,
  healthWindow,
  metricWindows,
  scopeWindows,
} from "../../../test/healthWindows";
import RepoMetricsTable from "./RepoMetricsTable";

function renderTable(windows: HealthScopeWindows = scopeWindows()) {
  return render(<RepoMetricsTable windows={windows} />);
}

describe("RepoMetricsTable — structure", () => {
  it("has the four columns Metric, Last 7 days, Last 30 days and 7d vs 30d", () => {
    renderTable();
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["Metric", "Last 7 days", "Last 30 days", "7d vs 30d"]);
  });

  it("groups the rows under Delivery, Quality and Unblocker", () => {
    renderTable();
    const order = screen.getAllByTestId(/^repo-metric-group-/).map((group) => group.dataset.testid);
    expect(order).toEqual([
      "repo-metric-group-delivery",
      "repo-metric-group-quality",
      "repo-metric-group-unblocker",
    ]);
    for (const title of ["Delivery", "Quality", "Unblocker"]) {
      expect(screen.getByText(title)).toHaveAttribute("scope", "rowgroup");
    }
    const delivery = screen.getByTestId("repo-metric-group-delivery");
    expect(within(delivery).getByTestId("repo-metric-row-outcome")).toBeInTheDocument();
    expect(within(delivery).getByTestId("repo-metric-row-build_success")).toBeInTheDocument();
    const quality = screen.getByTestId("repo-metric-group-quality");
    expect(within(quality).getByTestId("repo-metric-row-unproven_clauses")).toBeInTheDocument();
    const unblocker = screen.getByTestId("repo-metric-group-unblocker");
    expect(within(unblocker).getByTestId("repo-metric-row-rescue_load")).toBeInTheDocument();
  });

  it("does not render a mean fix cycles row", () => {
    renderTable();
    expect(screen.queryByText(/fix cycles/i)).not.toBeInTheDocument();
  });

  it("scrolls horizontally inside a labelled, keyboard-focusable region", () => {
    renderTable();
    const region = screen.getByRole("region", { name: /repository metrics table/i });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveClass("overflow-x-auto", "relative");
    expect(region).toContainElement(screen.getByTestId("repo-metrics-table"));
  });

  it("links a persistent one-line explanation to each metric name for screen readers", () => {
    renderTable();
    const row = screen.getByTestId("repo-metric-row-format_error");
    const header = within(row).getByRole("rowheader");
    const summary = screen.getByTestId("repo-metric-format_error-summary");
    expect(header.getAttribute("aria-describedby")).toBe(summary.id);
    const name = document.getElementById(header.getAttribute("aria-labelledby") ?? "");
    expect(name?.textContent).toBe("Format error rate");
    expect(summary).toBeVisible();
  });
});

describe("RepoMetricsTable — value cells", () => {
  it("shows a percentage with its sample size at full emphasis when data is enough", () => {
    renderTable(
      scopeWindows({
        build_success: metricWindows(healthWindow(0.9, 25), healthWindow(0.8, 100), "better"),
      }),
    );
    const cell = screen.getByTestId("repo-metric-build_success-7d");
    expect(cell).toHaveTextContent("90%");
    expect(cell).toHaveTextContent("n=25");
    expect(cell).toHaveAttribute("data-low-data", "false");
    expect(cell).not.toHaveTextContent("low data");
    expect(within(cell).getByText("90%")).toHaveClass("text-text-primary");
    expect(screen.getByTestId("repo-metric-build_success-30d")).toHaveTextContent("80% n=100");
  });

  it("dims a value under ten samples and marks it low data", () => {
    renderTable(
      scopeWindows({
        build_success: metricWindows(healthWindow(0.5, 4), healthWindow(0.8, 100)),
      }),
    );
    const cell = screen.getByTestId("repo-metric-build_success-7d");
    expect(cell).toHaveTextContent("50%");
    expect(cell).toHaveTextContent("n=4");
    expect(cell).toHaveTextContent("low data");
    expect(cell).toHaveAttribute("data-low-data", "true");
    expect(within(cell).getByText("50%")).toHaveClass("text-text-muted");
  });

  it("reads a window with no samples as not measured yet", () => {
    renderTable();
    expect(screen.getByTestId("repo-metric-outcome-7d")).toHaveTextContent("not measured yet");
    expect(screen.getByTestId("repo-metric-outcome-30d")).toHaveTextContent("not measured yet");
  });

  it("says a 30-day figure covers fewer days when the records do not reach back that far", () => {
    renderTable(
      scopeWindows({
        format_error: metricWindows(
          healthWindow(0.1, 40, 7),
          healthWindow(0.1, 60, 12),
          "insufficient",
          false,
        ),
      }),
    );
    expect(screen.getByTestId("repo-metric-format_error-30d")).toHaveTextContent("covers only 12 days");
    expect(screen.getByTestId("repo-metric-format_error-7d")).not.toHaveTextContent("covers");
  });
});

describe("RepoMetricsTable — outcome floor", () => {
  it("flags a below-floor outcome rate in words beside the value", () => {
    renderTable(
      scopeWindows({
        outcome: metricWindows(healthWindow(0.4, 30), healthWindow(0.3, 100), "same"),
      }),
    );
    expect(screen.getByTestId("repo-metric-outcome-7d-floor")).toHaveTextContent("below the 50% floor");
    expect(screen.getByTestId("repo-metric-outcome-30d-floor")).toHaveTextContent("below the 50% floor");
  });

  it("does not flag a below-floor rate that has too few samples to trust", () => {
    renderTable(
      scopeWindows({
        outcome: metricWindows(healthWindow(0.2, 3), healthWindow(0.3, 100)),
      }),
    );
    expect(screen.queryByTestId("repo-metric-outcome-7d-floor")).not.toBeInTheDocument();
    expect(screen.getByTestId("repo-metric-outcome-30d-floor")).toBeInTheDocument();
  });

  it("does not flag an outcome rate at or above the floor", () => {
    renderTable(
      scopeWindows({
        outcome: metricWindows(healthWindow(0.5, 30), healthWindow(0.9, 100), "worse"),
      }),
    );
    expect(screen.queryByTestId("repo-metric-outcome-7d-floor")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-metric-outcome-30d-floor")).not.toBeInTheDocument();
  });

  it("never flags a floor on rows other than the outcome rate", () => {
    renderTable(
      scopeWindows({
        autonomous: metricWindows(healthWindow(0.1, 30), healthWindow(0.1, 100)),
      }),
    );
    expect(screen.queryByTestId("repo-metric-autonomous-7d-floor")).not.toBeInTheDocument();
  });
});

describe("RepoMetricsTable — verdict cell", () => {
  it("shows a symbol and a word, and gives screen readers the full comparison sentence", () => {
    renderTable(
      scopeWindows({
        build_success: metricWindows(healthWindow(0.9, 25), healthWindow(0.7, 100), "better"),
      }),
    );
    const cell = screen.getByTestId("repo-metric-build_success-verdict");
    expect(cell).toHaveAttribute("data-verdict", "better");
    expect(cell).toHaveTextContent("▲ Better");
    expect(cell).toHaveTextContent(
      "Build success rate: last 7 days 90%, n=25; last 30 days 70%, n=100; the last 7 days are better than the last 30 days.",
    );
    expect(within(cell).getByText("▲ Better")).toHaveAttribute("aria-hidden", "true");
  });

  it.each([
    ["worse", "▼ Worse"],
    ["same", "≈ About the same"],
  ] as const)("shows %s with its symbol and word", (verdict, text) => {
    renderTable(
      scopeWindows({
        build_success: metricWindows(healthWindow(0.7, 25), healthWindow(0.7, 100), verdict),
      }),
    );
    expect(screen.getByTestId("repo-metric-build_success-verdict")).toHaveTextContent(text);
  });

  it("shows only a word when there is not enough data to compare", () => {
    renderTable();
    const cell = screen.getByTestId("repo-metric-outcome-verdict");
    expect(within(cell).getByText("Not enough data to compare")).toBeInTheDocument();
    expect(cell).not.toHaveTextContent(/[▲▼≈]/);
  });
});

describe("RepoMetricsTable — funnel stages", () => {
  const funnel = {
    d7: { ...ZERO_FUNNEL, dispatched: 12, not_crashed: 11, not_needed_human: 10, converged: 9, merged_pr: 8, dod_evidenced: 7 },
    d30: { ...ZERO_FUNNEL, dispatched: 6, not_crashed: 6, not_needed_human: 5, converged: 4, merged_pr: 3, dod_evidenced: 2 },
  };

  it("nests five indented stage rows under the outcome row, each showing x of N per window", () => {
    renderTable(scopeWindows({ funnel }));
    const delivery = screen.getByTestId("repo-metric-group-delivery");
    const rowIds = within(delivery)
      .getAllByRole("row")
      .map((row) => row.getAttribute("data-testid"));
    expect(rowIds).toEqual([
      null,
      "repo-metric-row-outcome",
      "repo-metric-funnel-not_crashed",
      "repo-metric-funnel-not_needed_human",
      "repo-metric-funnel-converged",
      "repo-metric-funnel-merged_pr",
      "repo-metric-funnel-dod_evidenced",
      "repo-metric-row-autonomous",
      "repo-metric-row-build_success",
    ]);
    expect(screen.getByTestId("repo-metric-funnel-merged_pr-7d")).toHaveTextContent("8 of 12");
    expect(screen.getByTestId("repo-metric-funnel-merged_pr-30d")).toHaveTextContent("3 of 6");
    const label = within(screen.getByTestId("repo-metric-funnel-merged_pr")).getByRole("rowheader");
    expect(label).toHaveTextContent("Merged");
    expect(label).toHaveClass("pl-6");
  });

  it("dims a stage count out of fewer than ten and marks it low data", () => {
    renderTable(scopeWindows({ funnel }));
    const enough = screen.getByTestId("repo-metric-funnel-converged-7d");
    expect(enough).toHaveAttribute("data-low-data", "false");
    expect(enough).not.toHaveTextContent("low data");
    const small = screen.getByTestId("repo-metric-funnel-converged-30d");
    expect(small).toHaveAttribute("data-low-data", "true");
    expect(small).toHaveTextContent("4 of 6 low data");
    expect(small).toHaveClass("text-text-muted");
  });

  it("reads an empty funnel as not measured yet and gives stage rows no verdict", () => {
    renderTable();
    const cell = screen.getByTestId("repo-metric-funnel-not_crashed-30d");
    expect(cell).toHaveTextContent("not measured yet");
    const row = screen.getByTestId("repo-metric-funnel-not_crashed");
    const cells = within(row).getAllByRole("cell");
    expect(cells).toHaveLength(3);
    expect(cells[2]).toBeEmptyDOMElement();
  });
});
