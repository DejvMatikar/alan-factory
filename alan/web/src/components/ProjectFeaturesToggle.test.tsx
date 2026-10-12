import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  PROJECT_FEATURES_STORAGE_KEY,
  readProjectFeaturesOn,
  writeProjectFeaturesOn,
} from "../projectFeatures";
import ProjectFeaturesToggle from "./ProjectFeaturesToggle";

const DESCRIPTION =
  "Shows or hides project features in the interface. Nothing in the data changes: projects, their repository bindings and their recorded spend are untouched.";

describe("ProjectFeaturesToggle", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("is not pressed by default, with no check glyph and no selected background", () => {
    render(<ProjectFeaturesToggle />);
    const toggle = screen.getByTestId("project-features-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("icon-check")).not.toBeInTheDocument();
    expect(toggle).not.toHaveClass("bg-surface-elevated");
  });

  it("shows pressed, the check glyph and the selected background when the preference is already on at mount", () => {
    writeProjectFeaturesOn(true);
    render(<ProjectFeaturesToggle />);
    const toggle = screen.getByTestId("project-features-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("icon-check")).toBeInTheDocument();
    expect(toggle).toHaveClass("bg-surface-elevated");
  });

  it("clicking while off stores the preference and flips to pressed without a reload", async () => {
    render(<ProjectFeaturesToggle />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-features-toggle"));
    expect(localStorage.getItem(PROJECT_FEATURES_STORAGE_KEY)).toBe("on");
    expect(screen.getByTestId("project-features-toggle")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("icon-check")).toBeInTheDocument();
  });

  it("clicking while on removes the stored preference and flips to not pressed", async () => {
    writeProjectFeaturesOn(true);
    render(<ProjectFeaturesToggle />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-features-toggle"));
    expect(localStorage.getItem(PROJECT_FEATURES_STORAGE_KEY)).toBeNull();
    expect(readProjectFeaturesOn()).toBe(false);
    expect(screen.getByTestId("project-features-toggle")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("icon-check")).not.toBeInTheDocument();
  });

  it("follows a same-tab write made elsewhere in the app", () => {
    render(<ProjectFeaturesToggle />);
    act(() => {
      writeProjectFeaturesOn(true);
    });
    expect(screen.getByTestId("project-features-toggle")).toHaveAttribute("aria-pressed", "true");
    act(() => {
      writeProjectFeaturesOn(false);
    });
    expect(screen.getByTestId("project-features-toggle")).toHaveAttribute("aria-pressed", "false");
  });

  it("resolves as a Projects toggle button in both states by role, name and pressed state", () => {
    render(<ProjectFeaturesToggle />);
    expect(screen.getByRole("button", { name: "Projects", pressed: false })).toBeInTheDocument();
    act(() => {
      writeProjectFeaturesOn(true);
    });
    expect(screen.getByRole("button", { name: "Projects", pressed: true })).toBeInTheDocument();
  });

  it("points aria-describedby at the plain-language description of what it changes", () => {
    render(<ProjectFeaturesToggle />);
    const toggle = screen.getByTestId("project-features-toggle");
    expect(toggle).toHaveAttribute("aria-describedby", "project-features-toggle-description");
    expect(document.getElementById("project-features-toggle-description")).toHaveTextContent(
      DESCRIPTION,
    );
  });

  it("carries the shared button's 44px touch-target sizing", () => {
    render(<ProjectFeaturesToggle />);
    const toggle = screen.getByTestId("project-features-toggle");
    expect(toggle).toHaveClass("min-h-11", "min-w-11");
  });
});
