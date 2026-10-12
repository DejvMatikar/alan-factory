import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunSummary } from "../api";
import RunPricing from "./RunPricing";

function makeSummary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    model: "opus",
    effort: "xhigh",
    system_prompt: "You are a helpful assistant.\nSecond line of detail.",
    total_cost_usd: 0.0017,
    total_output_tokens: 490,
    total_input_tokens: 320,
    tool_counts: [
      { name: "Read", count: 3 },
      { name: "Bash", count: 1 },
      { name: "Grep", count: 1 },
    ],
    ...overrides,
  };
}

describe("RunPricing", () => {
  it("renders nothing when given an empty runs array", () => {
    const { container } = render(<RunPricing runs={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when every run has a null or undefined summary", () => {
    const { container } = render(
      <RunPricing
        runs={[
          { key: "a", runSummary: null },
          { key: "b", runSummary: undefined },
        ]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a single run without any row suffix, matching the single-run wrapper", () => {
    render(<RunPricing runs={[{ runSummary: makeSummary() }]} />);
    expect(screen.getByTestId("run-summary-header")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-setup")).toHaveTextContent("opus · xhigh");
    expect(screen.getByTestId("run-summary-cost")).toHaveTextContent("$0.0017");
    expect(screen.queryByTestId("run-pricing")).not.toBeInTheDocument();
  });

  it("omits the label when a single run has no label", () => {
    render(<RunPricing runs={[{ runSummary: makeSummary() }]} />);
    expect(screen.queryByTestId("run-summary-label")).not.toBeInTheDocument();
  });

  it("renders multiple runs as distinct, independently keyed rows", () => {
    render(
      <RunPricing
        runs={[
          { key: "conversation", label: "Conversation", runSummary: makeSummary({ model: "opus" }) },
          {
            key: "collision",
            label: "Collision check",
            runSummary: makeSummary({ model: "haiku", effort: null, tool_counts: [] }),
          },
        ]}
      />,
    );
    expect(screen.getByTestId("run-pricing")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-header-conversation")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-header-collision")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-label-conversation")).toHaveTextContent("Conversation");
    expect(screen.getByTestId("run-summary-label-collision")).toHaveTextContent("Collision check");
    expect(screen.getByTestId("run-summary-setup-conversation")).toHaveTextContent("opus · xhigh");
    expect(screen.getByTestId("run-summary-setup-collision")).toHaveTextContent("haiku");
  });

  it("drops entries with a missing summary, rendering only the surviving row", () => {
    render(
      <RunPricing
        runs={[
          { key: "conversation", runSummary: makeSummary() },
          { key: "collision", runSummary: null },
        ]}
      />,
    );
    expect(screen.queryByTestId("run-pricing")).not.toBeInTheDocument();
    expect(screen.getByTestId("run-summary-header-conversation")).toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-header-collision")).not.toBeInTheDocument();
  });

  it("drops only the entries with a missing summary when multiple entries survive", () => {
    render(
      <RunPricing
        runs={[
          { key: "conversation", runSummary: makeSummary() },
          { key: "collision", runSummary: null },
          { key: "extra", runSummary: makeSummary({ model: "haiku" }) },
        ]}
      />,
    );
    expect(screen.getByTestId("run-pricing")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-header-conversation")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-header-extra")).toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-header-collision")).not.toBeInTheDocument();
  });

  it("falls back to an index-based suffix for multi-run entries without an explicit key", () => {
    render(
      <RunPricing
        runs={[{ runSummary: makeSummary({ model: "opus" }) }, { runSummary: makeSummary({ model: "haiku" }) }]}
      />,
    );
    expect(screen.getByTestId("run-summary-header-0")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-header-1")).toBeInTheDocument();
  });

  it("expands and collapses each row's system prompt independently", () => {
    render(
      <RunPricing
        runs={[
          { key: "a", runSummary: makeSummary({ system_prompt: "Prompt A" }) },
          { key: "b", runSummary: makeSummary({ system_prompt: "Prompt B" }) },
        ]}
      />,
    );
    const toggleA = screen.getByTestId("run-summary-prompt-toggle-a");
    const toggleB = screen.getByTestId("run-summary-prompt-toggle-b");

    fireEvent.click(toggleA);
    expect(screen.getByTestId("run-summary-prompt-full-a")).toHaveTextContent("Prompt A");
    expect(screen.getByTestId("run-summary-prompt-distilled-b")).toHaveTextContent("Prompt B");
    expect(toggleA).toHaveAttribute("aria-expanded", "true");
    expect(toggleB).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggleA);
    expect(screen.getByTestId("run-summary-prompt-distilled-a")).toBeInTheDocument();
  });

  it("distills to an empty string when the system prompt has no non-blank line", () => {
    render(<RunPricing runs={[{ runSummary: makeSummary({ system_prompt: "\n  \n\t\n" }) }]} />);
    expect(screen.getByTestId("run-summary-prompt-distilled")).toHaveTextContent("");
  });

  it("skips leading blank lines when distilling the system prompt", () => {
    render(
      <RunPricing
        runs={[{ runSummary: makeSummary({ system_prompt: "\n\n  real first line" }) }]}
      />,
    );
    expect(screen.getByTestId("run-summary-prompt-distilled")).toHaveTextContent(
      "real first line",
    );
  });

  it("truncates an overly long first line of the system prompt", () => {
    const longLine = "x".repeat(200);
    render(<RunPricing runs={[{ runSummary: makeSummary({ system_prompt: longLine }) }]} />);
    const text = screen.getByTestId("run-summary-prompt-distilled").textContent ?? "";
    expect(text.length).toBeLessThan(longLine.length);
    expect(text.endsWith("…")).toBe(true);
  });

  it("gives each row's system-prompt toggle a distinct accessible name", () => {
    render(
      <RunPricing
        runs={[
          { key: "conversation", label: "Conversation", runSummary: makeSummary({ model: "opus" }) },
          {
            key: "collision",
            label: "Collision check",
            runSummary: makeSummary({ model: "haiku", effort: null, tool_counts: [] }),
          },
        ]}
      />,
    );
    const conversationToggle = screen.getByTestId("run-summary-prompt-toggle-conversation");
    const collisionToggle = screen.getByTestId("run-summary-prompt-toggle-collision");

    expect(conversationToggle).toHaveAccessibleName("Show system prompt for Conversation");
    expect(collisionToggle).toHaveAccessibleName("Show system prompt for Collision check");

    fireEvent.click(conversationToggle);
    expect(conversationToggle).toHaveAccessibleName("Hide system prompt for Conversation");
    expect(collisionToggle).toHaveAccessibleName("Show system prompt for Collision check");
  });

  it("falls back to the model/effort setup for the toggle's accessible name when a row has no label", () => {
    render(<RunPricing runs={[{ runSummary: makeSummary({ model: "opus", effort: "xhigh" }) }]} />);
    expect(screen.getByTestId("run-summary-prompt-toggle")).toHaveAccessibleName(
      "Show system prompt for opus · xhigh",
    );
  });

  it("renders a tool-free auxiliary run with only model, tokens, and cost — no tool chips", () => {
    render(
      <RunPricing
        runs={[
          {
            key: "collision",
            label: "Collision check",
            runSummary: makeSummary({ model: "haiku", effort: null, tool_counts: [] }),
          },
        ]}
      />,
    );
    expect(screen.getByTestId("run-summary-setup-collision")).toHaveTextContent("haiku");
    expect(screen.getByTestId("run-summary-cost-collision")).toHaveTextContent("$0.0017");
    expect(screen.getByTestId("run-summary-output-tokens-collision")).toHaveTextContent(
      "490 output tokens",
    );
    expect(screen.queryByTestId("run-summary-tools-collision")).not.toBeInTheDocument();
  });

  it("renders tool chips for a run that does have tool activity", () => {
    render(<RunPricing runs={[{ key: "conversation", runSummary: makeSummary() }]} />);
    expect(screen.getByTestId("run-summary-tool-conversation-Read")).toHaveTextContent("Read × 3");
    expect(screen.getByTestId("run-summary-tool-conversation-Bash")).toHaveTextContent("Bash × 1");
  });

  it("wraps the tool chips by default and switches to a horizontally scrolling row from lg upward, reachable by keyboard", () => {
    render(<RunPricing runs={[{ key: "conversation", runSummary: makeSummary() }]} />);
    const tools = screen.getByTestId("run-summary-tools-conversation");
    expect(tools.tagName).toBe("FIELDSET");
    expect(tools.className).toContain("flex-wrap");
    expect(tools.className).toContain("lg:flex-nowrap");
    expect(tools.className).toContain("lg:overflow-x-auto");
    expect(tools).toHaveAttribute("tabIndex", "0");
    expect(tools).toHaveAccessibleName();
  });

  it("suppresses the system-prompt toggle and preview when showSystemPrompt is false", () => {
    render(<RunPricing runs={[{ runSummary: makeSummary() }]} showSystemPrompt={false} />);
    expect(screen.queryByTestId("run-summary-prompt-toggle")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-prompt-distilled")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-prompt-full")).not.toBeInTheDocument();
    expect(screen.getByTestId("run-summary-setup")).toHaveTextContent("opus · xhigh");
    expect(screen.getByTestId("run-summary-cost")).toHaveTextContent("$0.0017");
  });

  it("suppresses the system prompt across every row of a multi-run panel", () => {
    render(
      <RunPricing
        runs={[
          { key: "conversation", runSummary: makeSummary({ model: "opus" }) },
          { key: "collision", runSummary: makeSummary({ model: "haiku" }) },
        ]}
        showSystemPrompt={false}
      />,
    );
    expect(screen.queryByTestId("run-summary-prompt-toggle-conversation")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-prompt-toggle-collision")).not.toBeInTheDocument();
  });

  it("suppresses token figures when showTokens is false", () => {
    render(<RunPricing runs={[{ runSummary: makeSummary() }]} showTokens={false} />);
    expect(screen.queryByTestId("run-summary-output-tokens")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-input-tokens")).not.toBeInTheDocument();
    expect(screen.getByTestId("run-summary-cost")).toHaveTextContent("$0.0017");
  });

  it("suppresses token figures across every row of a multi-run panel", () => {
    render(
      <RunPricing
        runs={[
          { key: "conversation", runSummary: makeSummary({ model: "opus" }) },
          { key: "collision", runSummary: makeSummary({ model: "haiku" }) },
        ]}
        showTokens={false}
      />,
    );
    expect(screen.queryByTestId("run-summary-output-tokens-conversation")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-input-tokens-conversation")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-output-tokens-collision")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-input-tokens-collision")).not.toBeInTheDocument();
  });

  it("leaves the totals row with only the cost when tokens are suppressed", () => {
    render(<RunPricing runs={[{ runSummary: makeSummary() }]} showTokens={false} />);
    expect(screen.getByTestId("run-summary-totals").children).toHaveLength(1);
  });

  it("renders a dash for null cost and token totals without erroring", () => {
    render(
      <RunPricing
        runs={[
          {
            key: "collision",
            runSummary: makeSummary({
              total_cost_usd: null,
              total_output_tokens: null,
              total_input_tokens: null,
            }),
          },
        ]}
      />,
    );
    expect(screen.getByTestId("run-summary-cost-collision")).toHaveTextContent("—");
    expect(screen.getByTestId("run-summary-output-tokens-collision")).toHaveTextContent(
      "— output tokens",
    );
    expect(screen.getByTestId("run-summary-input-tokens-collision")).toHaveTextContent(
      "— billed input tokens",
    );
  });
});
