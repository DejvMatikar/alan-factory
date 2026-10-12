import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { OperatorTargets } from "../hooks/useOperatorTargets";
import type { TargetScope } from "../operatorCopy";
import TargetSelect from "./TargetSelect";

const READY: OperatorTargets = {
  status: "ready",
  options: [
    { value: "acme/widget", label: "acme/widget" },
    { value: "acme/gadget", label: "acme/gadget" },
  ],
  initial: "",
};

function renderSelect(
  targets: OperatorTargets,
  { scope = "repo", value = "" }: { scope?: TargetScope; value?: string } = {},
) {
  const onChange = vi.fn();
  render(<TargetSelect scope={scope} targets={targets} value={value} onChange={onChange} />);
  return onChange;
}

function optionTexts(): (string | null)[] {
  return within(screen.getByTestId("operator-target"))
    .getAllByRole("option")
    .map((option) => option.textContent);
}

describe("TargetSelect — a ready list", () => {
  it("is a native dropdown labelled for the scope, enabled, with a choose prompt ahead of the options", () => {
    renderSelect(READY);
    const select = screen.getByLabelText("Repository");
    expect(select.tagName).toBe("SELECT");
    expect(select).toBe(screen.getByTestId("operator-target"));
    expect(select).toBeEnabled();
    expect(optionTexts()).toEqual(["Choose a repository", "acme/widget", "acme/gadget"]);
    expect(select).not.toHaveAttribute("aria-describedby");
    expect(select).toHaveAttribute("aria-busy", "false");
  });

  it("labels the workspace dropdown for workspaces", () => {
    renderSelect(READY, { scope: "workspace" });
    expect(screen.getByLabelText("Workspace")).toBe(screen.getByTestId("operator-target"));
    expect(optionTexts()[0]).toBe("Choose a workspace");
  });

  it("cannot return to the choose prompt once an option is picked", () => {
    renderSelect(READY, { value: "acme/widget" });
    const prompt = within(screen.getByTestId("operator-target")).getByRole("option", {
      name: "Choose a repository",
    });
    expect(prompt).toBeDisabled();
  });

  it("shows the current value", () => {
    renderSelect(READY, { value: "acme/gadget" });
    expect(screen.getByTestId("operator-target")).toHaveValue("acme/gadget");
  });

  it("shows the choose prompt while nothing is chosen", () => {
    renderSelect(READY);
    expect(screen.getByTestId("operator-target")).toHaveValue("");
  });

  it("reports the picked value", async () => {
    const onChange = renderSelect(READY);
    await userEvent.setup().selectOptions(screen.getByTestId("operator-target"), "acme/gadget");
    expect(onChange).toHaveBeenCalledWith("acme/gadget");
  });

  it("shows no banner and no empty message", () => {
    renderSelect(READY);
    expect(screen.queryByTestId("operator-target-error")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-target-empty")).not.toBeInTheDocument();
  });
});

describe("TargetSelect — loading", () => {
  it("disables the dropdown, marks it busy and shows the loading label", () => {
    renderSelect({ status: "loading", options: [], initial: "" }, { scope: "workspace" });
    const select = screen.getByTestId("operator-target");
    expect(select).toBeDisabled();
    expect(select).toHaveAttribute("aria-busy", "true");
    expect(optionTexts()).toEqual(["Loading workspaces…"]);
    expect(screen.queryByTestId("operator-target-error")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operator-target-empty")).not.toBeInTheDocument();
  });

  it("names repositories while the repository list loads", () => {
    renderSelect({ status: "loading", options: [], initial: "" });
    expect(optionTexts()).toEqual(["Loading repositories…"]);
  });
});

describe("TargetSelect — a load failure", () => {
  it("shows an inline error banner, links it to the disabled dropdown and keeps no loading label", () => {
    renderSelect({ status: "failed", options: [], initial: "" }, { scope: "workspace" });
    const banner = screen.getByTestId("operator-target-error");
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner).toHaveTextContent("The workspace list could not be loaded.");
    const select = screen.getByTestId("operator-target");
    expect(select).toBeDisabled();
    expect(select).toHaveAccessibleDescription("The workspace list could not be loaded.");
    expect(optionTexts()).toEqual(["Choose a workspace"]);
  });

  it("names repositories when the repository list fails", () => {
    renderSelect({ status: "failed", options: [], initial: "" });
    expect(screen.getByTestId("operator-target-error")).toHaveTextContent(
      "The repository list could not be loaded.",
    );
  });
});

describe("TargetSelect — an empty list", () => {
  it("says nothing needs the operator right now for workspaces and links it to the disabled dropdown", () => {
    renderSelect({ status: "empty", options: [], initial: "" }, { scope: "workspace" });
    const message = screen.getByTestId("operator-target-empty");
    expect(message).toHaveTextContent("Nothing needs the operator right now.");
    const select = screen.getByTestId("operator-target");
    expect(select).toBeDisabled();
    expect(select).toHaveAccessibleDescription("Nothing needs the operator right now.");
    expect(screen.queryByTestId("operator-target-error")).not.toBeInTheDocument();
  });

  it("says no repository is connected for repositories", () => {
    renderSelect({ status: "empty", options: [], initial: "" });
    expect(screen.getByTestId("operator-target-empty")).toHaveTextContent(
      "No repository is connected yet.",
    );
  });
});
