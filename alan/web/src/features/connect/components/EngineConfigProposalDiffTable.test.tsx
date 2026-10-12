import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProposableEngineConfigField, ProposalFieldDiff } from "../../../api";
import { FIELD_LABEL } from "./engineConfigFieldSummary";
import type { WireEngineConfig } from "../../../test/engineFixtures";
import EngineConfigProposalDiffTable from "./EngineConfigProposalDiffTable";

const VALUES: Pick<WireEngineConfig, ProposableEngineConfigField> = {
  gate_commands: [["uv", "run", "pytest", "-q"]],
  warmup_commands: [["uv", "sync"]],
  base_tools: ["Read", "Grep"],
  team_tools: { implementation: ["Edit", "Write"] },
  roles: { implementer: { model: "opus", effort: "high" } },
  transition_graph: { research: ["architecture"], architecture: ["implementation"] },
  flow_policy: {
    verifier_teams: ["verification"],
    merge_teams: ["pr_merge"],
    terminal_teams: [],
    internal_only_teams: [],
    restricted_predecessors: {},
    revisit_cap: 3,
  },
};

function makeDiff(): ProposalFieldDiff[] {
  return [
    { field: "gate_commands", baseline: [["uv", "run", "pytest"]], proposed: VALUES.gate_commands, changed: true },
    { field: "warmup_commands", baseline: [["uv", "sync"]], proposed: VALUES.warmup_commands, changed: false },
    { field: "base_tools", baseline: ["Read"], proposed: VALUES.base_tools, changed: true },
    { field: "team_tools", baseline: { implementation: ["Edit"] }, proposed: VALUES.team_tools, changed: true },
    {
      field: "roles",
      baseline: { implementer: { model: "sonnet", effort: "high" } },
      proposed: VALUES.roles,
      changed: true,
    },
    {
      field: "transition_graph",
      baseline: { research: ["architecture"], architecture: [] },
      proposed: VALUES.transition_graph,
      changed: true,
    },
    { field: "flow_policy", baseline: null, proposed: VALUES.flow_policy, changed: true },
  ];
}

describe("EngineConfigProposalDiffTable", () => {
  it("renders exactly the seven proposable rows, pinned against FIELD_LABEL's key set — including the unchanged one", () => {
    render(
      <EngineConfigProposalDiffTable diff={makeDiff()} values={VALUES} disabled={false} onFieldChange={vi.fn()} />,
    );
    for (const field of Object.keys(FIELD_LABEL) as ProposableEngineConfigField[]) {
      expect(screen.getByTestId(`connect-step2-diff-${field.replaceAll("_", "-")}`)).toBeInTheDocument();
    }
    const table = screen.getByTestId("connect-step2-diff-table");
    const tbody = table.querySelector(":scope > tbody");
    expect(tbody?.querySelectorAll(":scope > tr")).toHaveLength(7);
  });

  it("renders Baseline and Now as the two data columns", () => {
    render(
      <EngineConfigProposalDiffTable diff={makeDiff()} values={VALUES} disabled={false} onFieldChange={vi.fn()} />,
    );
    const table = screen.getByTestId("connect-step2-diff-table");
    const headRow = table.querySelector(":scope > thead > tr");
    const headers = within(headRow as HTMLElement)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual(["Field", "Baseline", "Now"]);
  });

  it("renders every 'Now' cell as a focusable editor seeded from the current draft value, not a static preview", () => {
    render(
      <EngineConfigProposalDiffTable diff={makeDiff()} values={VALUES} disabled={false} onFieldChange={vi.fn()} />,
    );
    const baseToolsEditor = screen.getByTestId("connect-step2-diff-base-tools-now-editor");
    expect(baseToolsEditor).toHaveValue("Read\nGrep");
  });

  it("edits flow into onFieldChange, in place — no separate edit-mode toggle", () => {
    const onFieldChange = vi.fn();
    render(
      <EngineConfigProposalDiffTable diff={makeDiff()} values={VALUES} disabled={false} onFieldChange={onFieldChange} />,
    );
    const input = screen.getByTestId("connect-step2-diff-gate-commands-now-editor-input-0");
    fireEvent.change(input, { target: { value: "uv run pytest -q --maxfail=1" } });
    expect(onFieldChange).toHaveBeenLastCalledWith(
      "gate_commands",
      [["uv", "run", "pytest", "-q", "--maxfail=1"]],
    );
  });

  it("shows the baseline summary distinct from the editable 'Now' cell", () => {
    render(
      <EngineConfigProposalDiffTable diff={makeDiff()} values={VALUES} disabled={false} onFieldChange={vi.fn()} />,
    );
    expect(screen.getByTestId("connect-step2-diff-base-tools-baseline")).toHaveTextContent("Read");
    expect(screen.getByTestId("connect-step2-diff-base-tools-baseline")).not.toHaveTextContent("Grep");
  });

  it("disables every editor when disabled", () => {
    render(
      <EngineConfigProposalDiffTable diff={makeDiff()} values={VALUES} disabled onFieldChange={vi.fn()} />,
    );
    expect(screen.getByTestId("connect-step2-diff-base-tools-now-editor")).toBeDisabled();
  });
});
