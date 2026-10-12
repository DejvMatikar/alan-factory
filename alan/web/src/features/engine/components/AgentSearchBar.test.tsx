import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AgentFilters } from "../../../lib/agentSearch";
import AgentSearchBar, { type AgentSearchBarProps } from "./AgentSearchBar";

const NO_FILTERS: AgentFilters = { model: null, effort: null, kind: null };

function renderBar(overrides: Partial<AgentSearchBarProps> = {}) {
  const props: AgentSearchBarProps = {
    query: "",
    filters: NO_FILTERS,
    options: { models: ["opus", "sonnet"], efforts: ["CLI default", "high"] },
    shown: 3,
    total: 10,
    onQueryChange: vi.fn(),
    onFilterChange: vi.fn(),
    ...overrides,
  };
  render(<AgentSearchBar {...props} />);
  return props;
}

describe("AgentSearchBar", () => {
  it("is a labelled search landmark with a labelled search field", () => {
    renderBar();
    expect(screen.getByRole("search", { name: "Search agents" })).toBeInTheDocument();
    expect(
      screen.getByLabelText("Search agent names, models, efforts and prompt text"),
    ).toHaveAttribute("type", "search");
  });

  it("shows the current query and reports each change", async () => {
    const user = userEvent.setup();
    const props = renderBar({ query: "ab" });
    const input = screen.getByTestId("agent-search-input");
    expect(input).toHaveValue("ab");
    await user.type(input, "c");
    expect(props.onQueryChange).toHaveBeenLastCalledWith("abc");
  });

  it("announces the live result count politely", () => {
    renderBar();
    const count = screen.getByTestId("agent-search-count");
    expect(count).toHaveTextContent("Showing 3 of 10 agents");
    expect(count).toHaveAttribute("role", "status");
    expect(count).toHaveAttribute("aria-live", "polite");
  });

  it("offers every model, effort and kind as a labelled filter", () => {
    renderBar();
    const model = screen.getByLabelText("Model");
    expect([...model.querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "All models",
      "opus",
      "sonnet",
    ]);
    expect(
      [...screen.getByLabelText("Effort").querySelectorAll("option")].map((o) => o.textContent),
    ).toEqual(["All efforts", "CLI default", "high"]);
    expect(
      [...screen.getByLabelText("Kind").querySelectorAll("option")].map((o) => o.textContent),
    ).toEqual(["All kinds", "Team role", "Chat", "Background helper", "Operator-launched"]);
  });

  it("reports a chosen filter value and null when All is chosen again", async () => {
    const user = userEvent.setup();
    const props = renderBar({ filters: { model: "opus", effort: null, kind: null } });
    expect(screen.getByTestId("agent-filter-model")).toHaveValue("opus");
    await user.selectOptions(screen.getByTestId("agent-filter-effort"), "high");
    expect(props.onFilterChange).toHaveBeenLastCalledWith("effort", "high");
    await user.selectOptions(screen.getByTestId("agent-filter-kind"), "chat");
    expect(props.onFilterChange).toHaveBeenLastCalledWith("kind", "chat");
    await user.selectOptions(screen.getByTestId("agent-filter-model"), "All models");
    expect(props.onFilterChange).toHaveBeenLastCalledWith("model", null);
  });
});
