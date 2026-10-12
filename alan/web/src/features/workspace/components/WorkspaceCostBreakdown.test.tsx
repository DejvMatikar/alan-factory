import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { RunSummary, WorkspaceCostBreakdownEntry } from "../../../api";
import {
  activateDisclosure,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import WorkspaceCostBreakdown from "./WorkspaceCostBreakdown";

function aggregateSummary(totalCostUsd: number): RunSummary {
  return {
    model: "aggregate",
    effort: null,
    system_prompt: "",
    total_cost_usd: totalCostUsd,
    total_output_tokens: null,
    total_input_tokens: null,
    tool_counts: [],
  };
}

function entry(overrides: Partial<WorkspaceCostBreakdownEntry> = {}): WorkspaceCostBreakdownEntry {
  return {
    key: "team_runs",
    label: "Team & agent runs",
    run_summary: aggregateSummary(0.0012),
    ...overrides,
  };
}

function getSummary(): HTMLElement {
  const summary = screen.getByText("Cost breakdown").closest("summary");
  if (summary === null) throw new Error("summary element not found");
  return summary;
}

describe("WorkspaceCostBreakdown", () => {
  it("renders nothing when given an empty entries array", () => {
    const { container } = render(<WorkspaceCostBreakdown entries={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when every entry has a null run_summary", () => {
    const { container } = render(
      <WorkspaceCostBreakdown
        entries={[entry({ run_summary: null }), entry({ key: "discussion", run_summary: null })]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("collapses the breakdown by default, hiding per-category rows", () => {
    render(<WorkspaceCostBreakdown entries={[entry()]} />);
    expect(screen.getByTestId("workspace-cost-breakdown")).not.toHaveAttribute("open");
    expect(screen.getByTestId("run-summary-header-team_runs")).not.toBeVisible();
  });

  it("reveals category cost rows through the shared RunPricing component on click", async () => {
    const user = userEvent.setup();
    render(
      <WorkspaceCostBreakdown
        entries={[
          entry(),
          entry({
            key: "discussion",
            label: "Discussion chat",
            run_summary: aggregateSummary(0.0005),
          }),
        ]}
      />,
    );

    await user.click(screen.getByText("Cost breakdown"));

    expect(screen.getByTestId("workspace-cost-breakdown")).toHaveAttribute("open");
    expect(screen.getByTestId("run-summary-header-team_runs")).toBeVisible();
    expect(screen.getByTestId("run-summary-setup-team_runs")).toHaveTextContent(
      "Team & agent runs",
    );
    expect(screen.getByTestId("run-summary-cost-team_runs")).toHaveTextContent("$0.0012");
    expect(screen.getByTestId("run-summary-setup-discussion")).toHaveTextContent(
      "Discussion chat",
    );
    expect(screen.getByTestId("run-summary-cost-discussion")).toHaveTextContent("$0.0005");
  });

  it("drops categories with no spend recorded, keeping only the surviving rows", () => {
    render(
      <WorkspaceCostBreakdown
        entries={[entry(), entry({ key: "collision_check", run_summary: null })]}
      />,
    );
    fireEvent.click(screen.getByText("Cost breakdown"));
    expect(screen.getByTestId("run-summary-header-team_runs")).toBeVisible();
    expect(screen.queryByTestId("run-summary-header-collision_check")).not.toBeInTheDocument();
  });

  it("hides token figures entirely on cost-only aggregate rows, since aggregates never carry token data", () => {
    render(<WorkspaceCostBreakdown entries={[entry()]} />);
    fireEvent.click(screen.getByText("Cost breakdown"));
    expect(screen.queryByTestId("run-summary-output-tokens-team_runs")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-input-tokens-team_runs")).not.toBeInTheDocument();
    expect(screen.getByTestId("run-summary-cost-team_runs")).toHaveTextContent("$0.0012");
    expect(screen.queryByText(/output tokens/)).toBeNull();
    expect(screen.queryByText(/billed input tokens/)).toBeNull();
  });

  it("suppresses the system-prompt toggle, since it renders through RunPricing with showSystemPrompt false", () => {
    render(<WorkspaceCostBreakdown entries={[entry()]} />);
    fireEvent.click(screen.getByText("Cost breakdown"));
    expect(screen.queryByTestId("run-summary-prompt-toggle-team_runs")).not.toBeInTheDocument();
  });

  it("starts closed with ▸ beside the label", async () => {
    render(<WorkspaceCostBreakdown entries={[entry()]} />);
    await expectDisclosureState(screen.getByTestId("workspace-cost-breakdown"), false);
    expect(getSummary()).toHaveTextContent("Cost breakdown");
  });

  it("opens and closes on click, Enter and Space and ignores other keys", async () => {
    render(<WorkspaceCostBreakdown entries={[entry()]} />);
    const details = screen.getByTestId("workspace-cost-breakdown");
    const row = screen.getByTestId("run-summary-header-team_runs");

    await expectTogglesOnEveryActivation(details);
    expect(row).not.toBeVisible();

    activateDisclosure(details, "Enter");
    await expectDisclosureState(details, true);
    expect(row).toBeVisible();
  });

  it("marks the disclosure triangle as decorative to assistive technology", async () => {
    const user = userEvent.setup();
    render(<WorkspaceCostBreakdown entries={[entry()]} />);
    const glyph = getSummary().querySelector("span[aria-hidden='true']");
    expect(glyph).toHaveTextContent("▸");

    await user.click(screen.getByText("Cost breakdown"));

    await waitFor(() => expect(glyph).toHaveTextContent("▾"));
  });
});
