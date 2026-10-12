import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import KeyedListDictEditor from "./KeyedListDictEditor";

describe("KeyedListDictEditor", () => {
  it("renders an empty state when there are no keys", () => {
    render(<KeyedListDictEditor label="Transition graph" testId="graph" value={{}} onChange={vi.fn()} />);
    expect(screen.getByTestId("graph-empty")).toHaveTextContent("No entries.");
  });

  it("renders one row per key, sorted, each comma-joined", () => {
    render(
      <KeyedListDictEditor
        label="Transition graph"
        testId="graph"
        value={{ verification: ["implementation_fix", "pr_merge"], architecture: ["research"] }}
        onChange={vi.fn()}
      />,
    );
    const rows = screen.getAllByRole("textbox");
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "graph-input-architecture",
      "graph-input-verification",
    ]);
    expect(screen.getByTestId("graph-input-verification")).toHaveValue(
      "implementation_fix, pr_merge",
    );
  });

  it("does not offer any add/remove-key affordance", () => {
    render(
      <KeyedListDictEditor
        label="Transition graph"
        testId="graph"
        value={{ research: ["architecture"] }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("splits the edited comma-separated text back into a string list for the changed key only", () => {
    const onChange = vi.fn();
    render(
      <KeyedListDictEditor
        label="Transition graph"
        testId="graph"
        value={{ research: ["architecture"], decomposer: ["implementation"] }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByTestId("graph-input-research"), {
      target: { value: "architecture, implementation_plan" },
    });
    expect(onChange).toHaveBeenCalledWith({
      decomposer: ["implementation"],
      research: ["architecture", "implementation_plan"],
    });
  });

  it("supports editing a value down to an empty list", () => {
    const onChange = vi.fn();
    render(
      <KeyedListDictEditor
        label="Transition graph"
        testId="graph"
        value={{ deploy_verify: [] as string[], research: ["architecture"] }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByTestId("graph-input-research"), { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith({ deploy_verify: [], research: [] });
  });

  it("trims whitespace and drops empty tokens from comma-separated input", () => {
    const onChange = vi.fn();
    render(
      <KeyedListDictEditor label="Tools" testId="tools" value={{ implementation: [] }} onChange={onChange} />,
    );
    fireEvent.change(screen.getByTestId("tools-input-implementation"), {
      target: { value: " Edit ,, Write ," },
    });
    expect(onChange).toHaveBeenCalledWith({ implementation: ["Edit", "Write"] });
  });

  it("disables every input when disabled", () => {
    render(
      <KeyedListDictEditor
        label="Tools"
        testId="tools"
        value={{ implementation: ["Edit"] }}
        onChange={vi.fn()}
        disabled
      />,
    );
    expect(screen.getByTestId("tools-input-implementation")).toBeDisabled();
  });

  it("labels each row for keyboard/screen-reader operability", () => {
    render(
      <KeyedListDictEditor label="Tools" testId="tools" value={{ implementation: ["Edit"] }} onChange={vi.fn()} />,
    );
    expect(screen.getByLabelText("implementation")).toBe(
      screen.getByTestId("tools-input-implementation"),
    );
  });
});
