import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import RoleConfigTable from "./RoleConfigTable";

describe("RoleConfigTable", () => {
  it("renders an empty state when there are no roles", () => {
    render(<RoleConfigTable testId="roles" value={{}} onChange={vi.fn()} />);
    expect(screen.getByTestId("roles-empty")).toHaveTextContent("No roles.");
  });

  it("renders one row per role, sorted, with model and effort inputs", () => {
    render(
      <RoleConfigTable
        testId="roles"
        value={{
          judge: { model: "opus", effort: null },
          implementer: { model: "sonnet", effort: "xhigh" },
        }}
        onChange={vi.fn()}
      />,
    );
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(screen.getByTestId("roles-model-implementer")).toHaveValue("sonnet");
    expect(screen.getByTestId("roles-effort-implementer")).toHaveValue("xhigh");
    expect(screen.getByTestId("roles-model-judge")).toHaveValue("opus");
    expect(screen.getByTestId("roles-effort-judge")).toHaveValue("");
  });

  it("offers a sonnet/opus/haiku datalist for the model field", () => {
    render(
      <RoleConfigTable
        testId="roles"
        value={{ implementer: { model: "sonnet", effort: null } }}
        onChange={vi.fn()}
      />,
    );
    const modelInput = screen.getByTestId("roles-model-implementer");
    expect(modelInput).toHaveAttribute("list", "roles-model-options");
    const datalist = document.getElementById("roles-model-options");
    expect(datalist?.querySelectorAll("option")).toHaveLength(3);
  });

  it("updates only the changed role's model, leaving other roles untouched", () => {
    const onChange = vi.fn();
    render(
      <RoleConfigTable
        testId="roles"
        value={{
          implementer: { model: "sonnet", effort: "xhigh" },
          judge: { model: "opus", effort: null },
        }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByTestId("roles-model-implementer"), { target: { value: "haiku" } });
    expect(onChange).toHaveBeenCalledWith({
      implementer: { model: "haiku", effort: "xhigh" },
      judge: { model: "opus", effort: null },
    });
  });

  it("blank effort maps to null (None), not an empty string", () => {
    const onChange = vi.fn();
    render(
      <RoleConfigTable
        testId="roles"
        value={{ implementer: { model: "sonnet", effort: "xhigh" } }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByTestId("roles-effort-implementer"), { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith({ implementer: { model: "sonnet", effort: null } });
  });

  it("a non-blank effort round-trips as free text", () => {
    const onChange = vi.fn();
    render(
      <RoleConfigTable
        testId="roles"
        value={{ judge: { model: "opus", effort: null } }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByTestId("roles-effort-judge"), { target: { value: "medium" } });
    expect(onChange).toHaveBeenCalledWith({ judge: { model: "opus", effort: "medium" } });
  });

  it("disables every input when disabled", () => {
    render(
      <RoleConfigTable
        testId="roles"
        value={{ implementer: { model: "sonnet", effort: null } }}
        onChange={vi.fn()}
        disabled
      />,
    );
    expect(screen.getByTestId("roles-model-implementer")).toBeDisabled();
    expect(screen.getByTestId("roles-effort-implementer")).toBeDisabled();
  });

  it("labels each input for keyboard/screen-reader operability", () => {
    render(
      <RoleConfigTable
        testId="roles"
        value={{ implementer: { model: "sonnet", effort: null } }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("implementer model")).toBe(
      screen.getByTestId("roles-model-implementer"),
    );
    expect(screen.getByLabelText("implementer effort")).toBe(
      screen.getByTestId("roles-effort-implementer"),
    );
  });
});
