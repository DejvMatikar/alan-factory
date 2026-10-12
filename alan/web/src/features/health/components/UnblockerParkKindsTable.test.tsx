import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { UnblockShapeAggregate } from "../../../api";
import UnblockerParkKindsTable from "./UnblockerParkKindsTable";

function aggregate(overrides: Partial<UnblockShapeAggregate> = {}): UnblockShapeAggregate {
  return {
    shape: "merge_park",
    total: 6,
    workspaces: 4,
    cleared_workspaces: 2,
    still_stuck_workspaces: 1,
    gone_workspaces: 1,
    retried_workspaces: 1,
    attempt_outcomes: [
      { outcome: "acted", count: 3 },
      { outcome: "declined", count: 1 },
      { outcome: "failed", count: 1 },
    ],
    report_outcomes: [{ outcome: "needs_human", count: 1 }],
    ...overrides,
  };
}

function renderTable(aggregates: UnblockShapeAggregate[], scopeLabel = "all repositories") {
  return render(<UnblockerParkKindsTable aggregates={aggregates} scopeLabel={scopeLabel} />);
}

describe("UnblockerParkKindsTable", () => {
  it("renders nothing when there are no park kinds", () => {
    const { container } = renderTable([]);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the scope in the heading", () => {
    renderTable([aggregate()], "acme/widget");
    expect(screen.getByRole("heading", { level: 3, name: "Park kinds — acme/widget" })).toBeInTheDocument();
  });

  it("renders a table with one labelled column per figure", () => {
    renderTable([aggregate()]);
    const headers = screen.getAllByRole("columnheader").map((header) => header.textContent);
    expect(headers).toEqual([
      "Park kind",
      "Events",
      "Workspaces",
      "Cleared",
      "Still stuck",
      "Gone",
      "Retried",
      "Host attempts",
      "Gave up",
    ]);
  });

  it("renders one row per park kind keyed by its shape, after the header row", () => {
    renderTable([aggregate(), aggregate({ shape: "question_park" })]);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByTestId("unblocker-aggregate-merge_park")).toBeInTheDocument();
    expect(screen.getByTestId("unblocker-aggregate-question_park")).toBeInTheDocument();
  });

  it("puts the shape in a row header and the tallies in the numeric cells in column order", () => {
    renderTable([aggregate()]);
    const row = screen.getByTestId("unblocker-aggregate-merge_park");
    expect(within(row).getByRole("rowheader")).toHaveTextContent("merge_park");
    const cells = within(row).getAllByRole("cell");
    expect(cells.slice(0, 5).map((cell) => cell.textContent)).toEqual(["6", "4", "2", "1", "1"]);
    expect(cells[5]).toHaveTextContent("1");
  });

  it("keeps attempt outcomes and give-up outcomes in separate cells, never one sum", () => {
    renderTable([aggregate()]);
    const row = screen.getByTestId("unblocker-aggregate-merge_park");
    const cells = within(row).getAllByRole("cell");
    const attempts = cells[6]!;
    const gaveUp = cells[7]!;
    expect(within(attempts).getByTestId("aggregate-chip-unblocker-aggregate-merge_park-attempt-acted")).toHaveTextContent("3 acted");
    expect(within(attempts).getByTestId("aggregate-chip-unblocker-aggregate-merge_park-attempt-declined")).toHaveTextContent("1 declined");
    expect(within(attempts).getByTestId("aggregate-chip-unblocker-aggregate-merge_park-attempt-failed")).toHaveTextContent("1 failed");
    expect(within(gaveUp).getByTestId("aggregate-chip-unblocker-aggregate-merge_park-report-needs_human")).toHaveTextContent("1 needs human");
    expect(within(gaveUp).queryByText("3 acted")).not.toBeInTheDocument();
    expect(row).not.toHaveTextContent("5 attempts");
  });

  it("colours failed and needs-human outcomes red and leaves acted and declined neutral", () => {
    renderTable([aggregate()]);
    const chip = (suffix: string) =>
      screen.getByTestId(`aggregate-chip-unblocker-aggregate-merge_park-${suffix}`);
    expect(chip("attempt-failed").className).toContain("status-danger");
    expect(chip("report-needs_human").className).toContain("status-danger");
    expect(chip("attempt-acted").className).toContain("status-neutral");
    expect(chip("attempt-declined").className).toContain("status-neutral");
  });

  it("shows none for an outcome cell that has no entries", () => {
    renderTable([aggregate({ attempt_outcomes: [], report_outcomes: [] })]);
    const row = screen.getByTestId("unblocker-aggregate-merge_park");
    expect(within(row).getAllByText("none")).toHaveLength(2);
  });

  it("explains what cleared means in its column header without treating it as proof", () => {
    renderTable([aggregate()]);
    expect(screen.getByRole("columnheader", { name: "Cleared" })).toHaveAttribute(
      "title",
      expect.stringMatching(/not proof/i),
    );
  });

  it("scrolls horizontally inside a keyboard-focusable labelled region instead of overflowing", () => {
    renderTable([aggregate()]);
    const region = screen.getByLabelText("Park kinds table, scroll horizontally for more columns");
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.className).toContain("overflow-x-auto");
    expect(region.className).toContain("relative");
    expect(within(region).getByRole("table")).toBeInTheDocument();
  });

  it("offers no control", () => {
    renderTable([aggregate()]);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
