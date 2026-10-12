import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RepoHealthLegend from "./RepoHealthLegend";

describe("RepoHealthLegend", () => {
  it("says the rates cover 30 days and explains each verdict symbol and the five point band", () => {
    render(<RepoHealthLegend />);
    const text = screen.getByTestId("repo-health-legend-window").textContent ?? "";
    expect(text).toContain("Rates cover the last 30 days");
    expect(text).toContain("▲ better");
    expect(text).toContain("▼ worse");
    expect(text).toContain("≈ about the same");
    expect(text).toContain("within ±5 percentage points");
    expect(text).toContain("No symbol means there is not enough data to compare");
  });

  it("explains dimmed values and the n= sample size", () => {
    render(<RepoHealthLegend />);
    const text = screen.getByTestId("repo-health-legend-low-data").textContent ?? "";
    expect(text).toContain("A dimmed value has fewer than 10 samples");
    expect(text).toContain("n= is the number of samples");
  });

  it("explains the dash", () => {
    render(<RepoHealthLegend />);
    expect(screen.getByTestId("repo-health-legend-missing")).toHaveTextContent(
      "— means not measured yet.",
    );
  });

  it("gives a one-line definition for each column", () => {
    render(<RepoHealthLegend />);
    const columns = screen.getByTestId("repo-health-legend-columns");
    const terms = within(columns)
      .getAllByRole("term")
      .map((term) => term.textContent);
    expect(terms).toEqual([
      "Findings:",
      "Build success:",
      "30-day outcome:",
      "Autonomous:",
      "In flight:",
      "First-pass:",
    ]);
    expect(within(columns).getAllByRole("definition")).toHaveLength(6);
  });

  it("is a labelled region so it is reachable by assistive technology", () => {
    render(<RepoHealthLegend />);
    expect(
      screen.getByRole("region", { name: "How to read the repository health table" }),
    ).toBe(screen.getByTestId("repo-health-legend"));
  });
});
