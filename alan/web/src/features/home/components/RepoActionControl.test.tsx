import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { GearIcon } from "../../../components/Button";
import RepoActionControl, { type RepoActionControlProps } from "./RepoActionControl";

function controlTree(overrides: Partial<RepoActionControlProps> = {}) {
  const props: RepoActionControlProps = {
    repoKey: "acme/widget",
    label: "Maintenance",
    description: "Immediately starts maintenance work that costs money.",
    testId: "repo-action-maintenance",
    ...overrides,
  };
  return (
    <MemoryRouter>
      <RepoActionControl {...props} />
    </MemoryRouter>
  );
}

function renderControl(overrides: Partial<RepoActionControlProps> = {}) {
  return render(controlTree(overrides));
}

describe("RepoActionControl — testid stability post-extraction", () => {
  it("composes the same ${testId}-${repoKey} testid for the button variant", () => {
    renderControl({ onClick: vi.fn(), testId: "repo-action-maintenance", repoKey: "acme/widget" });
    expect(screen.getByTestId("repo-action-maintenance-acme/widget")).toBeInTheDocument();
  });

  it("composes the same ${testId}-${repoKey} testid for the link variant", () => {
    renderControl({ testId: "repo-action-settings", repoKey: "acme/widget", linkTo: "/repos/acme/widget/config" });
    expect(screen.getByTestId("repo-action-settings-acme/widget")).toBeInTheDocument();
  });
});

describe("RepoActionControl — Maintenance variant", () => {
  it("renders as a real button wired to onClick when enabled", async () => {
    const onClick = vi.fn();
    renderControl({ onClick });
    const button = screen.getByTestId("repo-action-maintenance-acme/widget");
    expect(button.tagName).toBe("BUTTON");
    const user = userEvent.setup();
    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders as a ghost link when gated via linkTo, never firing a click callback", () => {
    renderControl({ linkTo: "/connect/acme/widget/posture" });
    const link = screen.getByTestId("repo-action-maintenance-acme/widget");
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("data-variant")).toBe("ghost");
    expect(link).toHaveAttribute("href", "/connect/acme/widget/posture");
  });

  it("closes the hover-revealed description when posture resolution swaps the gated link for the live button under a focused control", () => {
    const onClick = vi.fn();
    const { rerender } = renderControl({ linkTo: "/connect/acme/widget/posture" });
    const link = screen.getByTestId("repo-action-maintenance-acme/widget");
    act(() => {
      link.focus();
    });
    const describedBy = link.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    const description = document.getElementById(describedBy ?? "");
    expect(description).not.toBeNull();
    expect(description).not.toHaveClass("sr-only");
    rerender(controlTree({ onClick }));
    const button = screen.getByTestId("repo-action-maintenance-acme/widget");
    const liveDescribedBy = button.getAttribute("aria-describedby");
    expect(liveDescribedBy).not.toBeNull();
    const liveDescription = document.getElementById(liveDescribedBy ?? "");
    expect(liveDescription).not.toBeNull();
    expect(liveDescription).toHaveClass("sr-only");
  });

  it("marks the button aria-disabled while a dispatch is already in flight, without removing it from the tab order, and guards the click", async () => {
    const onClick = vi.fn();
    renderControl({ onClick, disabled: true });
    const button = screen.getByTestId("repo-action-maintenance-acme/widget");
    expect(button).not.toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
    const user = userEvent.setup();
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("carries aria-busy through to the rendered button", () => {
    renderControl({ onClick: vi.fn(), ariaBusy: true });
    expect(screen.getByTestId("repo-action-maintenance-acme/widget")).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });

  it("uses the given ariaLabel as the accessible name instead of the visible label, disambiguating it from a same-named global control", () => {
    renderControl({ onClick: vi.fn(), ariaLabel: "Maintenance for acme/widget" });
    const button = screen.getByTestId("repo-action-maintenance-acme/widget");
    expect(button).toHaveAccessibleName("Maintenance for acme/widget");
  });

  it("falls back to the visible label as the accessible name when ariaLabel is omitted", () => {
    renderControl({ onClick: vi.fn() });
    const button = screen.getByTestId("repo-action-maintenance-acme/widget");
    expect(button).toHaveAccessibleName("Maintenance");
  });
});

describe("RepoActionControl — Hide variant", () => {
  it("renders Hide as aria-disabled (not natively disabled) for the default repository, keeping it focusable so its hover-revealed description is reachable by keyboard", () => {
    renderControl({
      testId: "repo-action-hide",
      label: "Hide",
      description: "The default repository can't be hidden.",
      onClick: vi.fn(),
      disabled: true,
    });
    const hide = screen.getByTestId("repo-action-hide-acme/widget");
    expect(hide).not.toBeDisabled();
    expect(hide).toHaveAttribute("aria-disabled", "true");
    expect(hide).toHaveTextContent("Hide");
    const describedBy = hide.getAttribute("aria-describedby");
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent("can't be hidden");
  });

  it("wires the consequence description via aria-describedby, always mounted in the DOM even while hidden", () => {
    renderControl({
      testId: "repo-action-hide",
      label: "Hide",
      description: "Parks this repository: attempts to stop its work and hides it everywhere.",
      onClick: vi.fn(),
    });
    const hide = screen.getByTestId("repo-action-hide-acme/widget");
    const describedBy = hide.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    const description = document.getElementById(describedBy ?? "");
    expect(description).toHaveTextContent("Parks this repository");
    expect(description).toHaveClass("sr-only");
  });
});

describe("RepoActionControl — Settings variant", () => {
  it("renders as a ghost link to the engine config screen", () => {
    renderControl({
      testId: "repo-action-settings",
      label: "Settings",
      description: "Read how this engine is configured.",
      linkTo: "/repos/acme/widget/config",
    });
    const settings = screen.getByTestId("repo-action-settings-acme/widget");
    expect(settings.tagName).toBe("A");
    expect(settings).toHaveAttribute("href", "/repos/acme/widget/config");
  });

  it("uses the given ariaLabel as the accessible name for the link variant too", () => {
    renderControl({
      testId: "repo-action-settings",
      label: "Settings",
      description: "Read how this engine is configured.",
      linkTo: "/repos/acme/widget/config",
      ariaLabel: "Settings for acme/widget",
    });
    const settings = screen.getByTestId("repo-action-settings-acme/widget");
    expect(settings).toHaveAccessibleName("Settings for acme/widget");
  });

  it("renders for the default engine's own repository row with no reduced variant", () => {
    renderControl({
      repoKey: "DejvMatikar/alan",
      testId: "repo-action-settings",
      label: "Settings",
      description: "Read how this engine is configured.",
      linkTo: "/repos/DejvMatikar/alan/config",
    });
    const settings = screen.getByTestId("repo-action-settings-DejvMatikar/alan");
    expect(settings).not.toBeDisabled();
  });

  it("renders a gear icon instead of the label text when an icon is given, keeping the accessible name", () => {
    renderControl({
      testId: "repo-action-settings",
      label: "Settings",
      description: "Read how this engine is configured.",
      linkTo: "/repos/acme/widget/config",
      ariaLabel: "Settings for acme/widget",
      icon: <GearIcon />,
    });
    const settings = screen.getByTestId("repo-action-settings-acme/widget");
    expect(settings).not.toHaveTextContent("Settings");
    expect(settings.querySelector('[data-testid="icon-gear"]')).not.toBeNull();
    expect(settings).toHaveAccessibleName("Settings for acme/widget");
    expect(settings.className).toContain("min-h-11");
    expect(settings.className).toContain("min-w-11");
  });
});
