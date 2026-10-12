import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import SubduedActionControl, {
  SUBDUED_WEIGHT_CLASS,
  type SubduedActionControlBaseProps,
  type SubduedActionControlProps,
} from "./SubduedActionControl";

function renderControl(overrides: Partial<SubduedActionControlBaseProps> = {}) {
  const props: SubduedActionControlProps = {
    testId: "subdued-action",
    label: "Stop",
    description: "Stop this workspace's running job.",
    ...overrides,
  };
  return render(
    <MemoryRouter>
      <SubduedActionControl {...props} />
    </MemoryRouter>,
  );
}

describe("SubduedActionControl — weight", () => {
  it("carries the dimmed-by-default, brighten-on-hover weight class", () => {
    renderControl();
    expect(screen.getByTestId("subdued-action").className).toContain(SUBDUED_WEIGHT_CLASS);
  });

  it("renders no native title attribute", () => {
    renderControl();
    expect(screen.getByTestId("subdued-action")).not.toHaveAttribute("title");
  });
});

describe("SubduedActionControl — click", () => {
  it("fires onClick when enabled", async () => {
    const onClick = vi.fn();
    renderControl({ onClick });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("subdued-action"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("guards the click while aria-disabled, without removing it from the tab order", async () => {
    const onClick = vi.fn();
    renderControl({ onClick, disabled: true });
    const button = screen.getByTestId("subdued-action");
    expect(button).not.toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
    const user = userEvent.setup();
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("carries aria-busy through to the rendered button", () => {
    renderControl({ ariaBusy: true });
    expect(screen.getByTestId("subdued-action")).toHaveAttribute("aria-busy", "true");
  });
});

describe("SubduedActionControl — description reveal", () => {
  it("reveals the description on pointer hover", async () => {
    renderControl();
    const button = screen.getByTestId("subdued-action");
    const describedBy = button.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    const description = document.getElementById(describedBy ?? "");
    expect(description).toHaveClass("sr-only");
    const user = userEvent.setup();
    await user.hover(button);
    expect(description).not.toHaveClass("sr-only");
  });

  it("reveals the description on keyboard focus and dismisses it on blur", async () => {
    renderControl();
    const button = screen.getByTestId("subdued-action");
    const describedBy = button.getAttribute("aria-describedby");
    const description = document.getElementById(describedBy ?? "");
    const user = userEvent.setup();
    await user.tab();
    expect(button).toHaveFocus();
    expect(description).not.toHaveClass("sr-only");
    await user.tab();
    expect(description).toHaveClass("sr-only");
  });

  it("dismisses an open description with Escape", async () => {
    renderControl();
    const button = screen.getByTestId("subdued-action");
    const describedBy = button.getAttribute("aria-describedby");
    const description = document.getElementById(describedBy ?? "");
    const user = userEvent.setup();
    await user.tab();
    expect(description).not.toHaveClass("sr-only");
    await user.keyboard("{Escape}");
    expect(description).toHaveClass("sr-only");
  });

  it("uses the given ariaLabel as the accessible name when provided", () => {
    renderControl({ ariaLabel: "Stop e2e-task-1" });
    expect(screen.getByTestId("subdued-action")).toHaveAccessibleName("Stop e2e-task-1");
  });

  it("falls back to the visible label as the accessible name when ariaLabel is omitted", () => {
    renderControl();
    expect(screen.getByTestId("subdued-action")).toHaveAccessibleName("Stop");
  });
});

describe("SubduedActionControl — icon variant", () => {
  function renderIconControl(overrides: Partial<SubduedActionControlBaseProps> = {}) {
    const props: SubduedActionControlProps = {
      testId: "subdued-action",
      label: "Stop",
      description: "Stop this workspace's running job.",
      icon: <svg data-testid="test-icon" aria-hidden="true" />,
      variant: "action",
      ariaLabel: "Stop e2e-task-1",
      ...overrides,
    };
    return render(
      <MemoryRouter>
        <SubduedActionControl {...props} />
      </MemoryRouter>,
    );
  }

  it("renders the icon rather than the visible label text", () => {
    renderIconControl();
    expect(screen.getByTestId("test-icon")).toBeInTheDocument();
    expect(screen.getByTestId("subdued-action")).not.toHaveTextContent("Stop");
  });

  it("carries the requested variant's blue action colour and tinted hover area, not the dimmed label weight class", () => {
    renderIconControl();
    const button = screen.getByTestId("subdued-action");
    expect(button.className).toContain("text-btn-action");
    expect(button.className).toContain("hover:bg-btn-action/15");
    expect(button.className).not.toContain(SUBDUED_WEIGHT_CLASS);
  });

  it("uses the given ariaLabel as the accessible name", () => {
    renderIconControl();
    expect(screen.getByTestId("subdued-action")).toHaveAccessibleName("Stop e2e-task-1");
  });

  it("falls back to the visible label as the accessible name when ariaLabel is omitted", () => {
    renderIconControl({ ariaLabel: undefined });
    expect(screen.getByTestId("subdued-action")).toHaveAccessibleName("Stop");
  });

  it("carries aria-busy and aria-disabled through to the icon button, and blocks the click while disabled", async () => {
    const onClick = vi.fn();
    renderIconControl({ onClick, ariaBusy: true, disabled: true });
    const button = screen.getByTestId("subdued-action");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).not.toBeDisabled();
    const user = userEvent.setup();
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("reveals the description on hover, same as the label variant", async () => {
    renderIconControl();
    const button = screen.getByTestId("subdued-action");
    const describedBy = button.getAttribute("aria-describedby");
    const description = document.getElementById(describedBy ?? "");
    expect(description).toHaveClass("sr-only");
    const user = userEvent.setup();
    await user.hover(button);
    expect(description).not.toHaveClass("sr-only");
  });
});
