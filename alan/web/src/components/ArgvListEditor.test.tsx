import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ArgvListEditor from "./ArgvListEditor";

describe("ArgvListEditor", () => {
  it("renders an empty state and no rows when value is empty", () => {
    render(<ArgvListEditor label="Gate commands" testId="gate" value={[]} onChange={vi.fn()} />);
    expect(screen.getByTestId("gate-empty")).toHaveTextContent("No commands.");
    expect(screen.queryByTestId("gate-input-0")).not.toBeInTheDocument();
  });

  it("renders each command space-joined in its own input", () => {
    render(
      <ArgvListEditor
        label="Gate commands"
        testId="gate"
        value={[["uv", "run", "pytest"], ["ruff", "check", "."]]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("gate-input-0")).toHaveValue("uv run pytest");
    expect(screen.getByTestId("gate-input-1")).toHaveValue("ruff check .");
  });

  it("splits edited text back into argv tokens on change", () => {
    const onChange = vi.fn();
    render(
      <ArgvListEditor label="Gate commands" testId="gate" value={[["uv", "run"]]} onChange={onChange} />,
    );
    fireEvent.change(screen.getByTestId("gate-input-0"), { target: { value: "npm run lint" } });
    expect(onChange).toHaveBeenCalledWith([["npm", "run", "lint"]]);
  });

  it("editing one row leaves every other row's command untouched", () => {
    const onChange = vi.fn();
    render(
      <ArgvListEditor
        label="Gate commands"
        testId="gate"
        value={[["uv", "run", "pytest"], ["ruff", "check", "."]]}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByTestId("gate-input-1"), { target: { value: "ruff format" } });
    expect(onChange).toHaveBeenCalledWith([["uv", "run", "pytest"], ["ruff", "format"]]);
  });

  it("adds a new empty command row", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ArgvListEditor label="Warmup commands" testId="warmup" value={[["uv", "sync"]]} onChange={onChange} />,
    );
    await user.click(screen.getByTestId("warmup-add"));
    expect(onChange).toHaveBeenCalledWith([["uv", "sync"], []]);
  });

  it("removes a command row, supporting editing down to an empty list", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ArgvListEditor label="Gate commands" testId="gate" value={[["uv", "run", "pytest"]]} onChange={onChange} />,
    );
    await user.click(screen.getByTestId("gate-remove-0"));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("removing one row of several leaves the remaining rows' commands intact", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ArgvListEditor
        label="Gate commands"
        testId="gate"
        value={[["uv", "run", "pytest"], ["ruff", "check", "."]]}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByTestId("gate-remove-0"));
    expect(onChange).toHaveBeenCalledWith([["ruff", "check", "."]]);
  });

  it("disables inputs and buttons when disabled", () => {
    render(
      <ArgvListEditor
        label="Gate commands"
        testId="gate"
        value={[["uv", "run"]]}
        onChange={vi.fn()}
        disabled
      />,
    );
    expect(screen.getByTestId("gate-input-0")).toBeDisabled();
    expect(screen.getByTestId("gate-remove-0")).toBeDisabled();
    expect(screen.getByTestId("gate-add")).toBeDisabled();
  });

  it("labels each row input for keyboard/screen-reader operability", () => {
    render(
      <ArgvListEditor label="Gate commands" testId="gate" value={[["uv", "run"]]} onChange={vi.fn()} />,
    );
    expect(screen.getByLabelText("Gate commands command 1")).toBe(screen.getByTestId("gate-input-0"));
  });
});
