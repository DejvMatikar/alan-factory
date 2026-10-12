import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { EngineFlowGraphPolicy, EngineRoleConfig } from "../api";
import EngineConfigFieldEditor from "./EngineConfigFieldEditor";

const FLOW_POLICY: EngineFlowGraphPolicy = {
  verifier_teams: ["verification"],
  merge_teams: ["pr_merge"],
  terminal_teams: [],
  internal_only_teams: [],
  restricted_predecessors: { research: ["architecture"] },
  revisit_cap: 3,
};

describe("EngineConfigFieldEditor", () => {
  it("renders RoleConfigTable for roles and reports edits", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const roles: Record<string, EngineRoleConfig> = { implementer: { model: "opus", effort: "high" } };
    render(
      <EngineConfigFieldEditor field="roles" value={roles} disabled={false} onChange={onChange} testId="ed" />,
    );
    const modelInput = screen.getByTestId("ed-model-implementer");
    await user.clear(modelInput);
    await user.type(modelInput, "haiku");
    expect(onChange).toHaveBeenCalled();
  });

  it("renders ArgvListEditor for gate_commands and warmup_commands", () => {
    render(
      <EngineConfigFieldEditor
        field="gate_commands"
        value={[["uv", "run", "pytest"]]}
        disabled={false}
        onChange={vi.fn()}
        testId="ed-gate"
      />,
    );
    expect(screen.getByTestId("ed-gate-input-0")).toHaveValue("uv run pytest");
  });

  it("renders KeyedListDictEditor for team_tools and transition_graph", () => {
    render(
      <EngineConfigFieldEditor
        field="team_tools"
        value={{ implementation: ["Edit"] }}
        disabled={false}
        onChange={vi.fn()}
        testId="ed-tools"
      />,
    );
    expect(screen.getByTestId("ed-tools-input-implementation")).toHaveValue("Edit");
  });

  it("renders a labelled textarea for base_tools and edits flow through onChange", () => {
    const onChange = vi.fn();
    render(
      <EngineConfigFieldEditor
        field="base_tools"
        value={["Read"]}
        disabled={false}
        onChange={onChange}
        testId="ed-base"
      />,
    );
    const textarea = screen.getByTestId("ed-base");
    expect(screen.getByText("Base tools (one per line)")).toBeInTheDocument();
    fireEvent.change(textarea, { target: { value: "Read\nGrep" } });
    expect(onChange).toHaveBeenLastCalledWith(["Read", "Grep"]);
  });

  it("renders flow_policy's team lists via KeyedListDictEditor and an editable revisit cap", () => {
    const onChange = vi.fn();
    render(
      <EngineConfigFieldEditor
        field="flow_policy"
        value={FLOW_POLICY}
        disabled={false}
        onChange={onChange}
        testId="ed-flow"
      />,
    );
    expect(screen.getByTestId("ed-flow-teams-input-verifier_teams")).toHaveValue("verification");
    const revisitCap = screen.getByTestId("ed-flow-revisit-cap");
    expect(revisitCap).toHaveValue("3");
    fireEvent.change(revisitCap, { target: { value: "5" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ revisit_cap: 5 }));
  });

  it("edits a flow_policy team list, carrying every other team key through as a real array", () => {
    const onChange = vi.fn();
    render(
      <EngineConfigFieldEditor field="flow_policy" value={FLOW_POLICY} disabled={false} onChange={onChange} testId="ed-flow" />,
    );
    fireEvent.change(screen.getByTestId("ed-flow-teams-input-verifier_teams"), {
      target: { value: "verification, review" },
    });
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        verifier_teams: ["verification", "review"],
        merge_teams: FLOW_POLICY.merge_teams,
        terminal_teams: FLOW_POLICY.terminal_teams,
        internal_only_teams: FLOW_POLICY.internal_only_teams,
      }),
    );
  });

  it("ignores a non-numeric revisit cap edit rather than calling onChange with NaN", () => {
    const onChange = vi.fn();
    render(
      <EngineConfigFieldEditor field="flow_policy" value={FLOW_POLICY} disabled={false} onChange={onChange} testId="ed-flow" />,
    );
    fireEvent.change(screen.getByTestId("ed-flow-revisit-cap"), { target: { value: "not-a-number" } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("ignores a negative, decimal, or blank revisit cap edit rather than coercing it", () => {
    const onChange = vi.fn();
    render(
      <EngineConfigFieldEditor field="flow_policy" value={FLOW_POLICY} disabled={false} onChange={onChange} testId="ed-flow" />,
    );
    const revisitCap = screen.getByTestId("ed-flow-revisit-cap");
    fireEvent.change(revisitCap, { target: { value: "-1" } });
    fireEvent.change(revisitCap, { target: { value: "1.5" } });
    fireEvent.change(revisitCap, { target: { value: "" } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("renders a placeholder, not a crash, when flow_policy is null", () => {
    render(
      <EngineConfigFieldEditor field="flow_policy" value={null} disabled={false} onChange={vi.fn()} testId="ed-flow-null" />,
    );
    expect(screen.getByTestId("ed-flow-null")).toHaveTextContent("No flow policy to edit.");
  });

  it("disables every rendered control when disabled", () => {
    render(
      <EngineConfigFieldEditor
        field="base_tools"
        value={["Read"]}
        disabled
        onChange={vi.fn()}
        testId="ed-disabled"
      />,
    );
    expect(screen.getByTestId("ed-disabled")).toBeDisabled();
  });
});
