import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type React from "react";

import DescribedButton from "./DescribedButton";

function renderButton(overrides: Partial<React.ComponentProps<typeof DescribedButton>> = {}) {
  const onClick = vi.fn();
  render(
    <DescribedButton
      variant="ghost"
      label="Archive"
      description="Moves this workspace out of the active list."
      testId="archive-button"
      onClick={onClick}
      {...overrides}
    />,
  );
  return { onClick, button: screen.getByTestId(overrides.testId ?? "archive-button") };
}

function descriptionOf(button: HTMLElement): HTMLElement | null {
  return document.getElementById(button.getAttribute("aria-describedby") ?? "");
}

describe("DescribedButton", () => {
  it("shows a visible text label", () => {
    const { button } = renderButton();
    expect(button).toHaveTextContent("Archive");
    expect(button).toHaveAccessibleName("Archive");
  });

  it("renders the ghost variant", () => {
    const { button } = renderButton({ variant: "ghost" });
    expect(button).toHaveAttribute("data-variant", "ghost");
  });

  it("renders the destroy variant", () => {
    const { button } = renderButton({ variant: "destroy", label: "Delete" });
    expect(button).toHaveAttribute("data-variant", "destroy");
  });

  it("calls onClick when clicked", async () => {
    const { button, onClick } = renderButton();
    await userEvent.setup().click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("wires the description through aria-describedby, screen-reader-only at rest", () => {
    const { button } = renderButton();
    const description = descriptionOf(button);
    expect(description).toHaveTextContent("Moves this workspace out of the active list.");
    expect(description).toHaveAttribute("role", "tooltip");
    expect(description).toHaveClass("sr-only");
  });

  it("reveals the description on keyboard focus", () => {
    const { button } = renderButton();
    act(() => {
      button.focus();
    });
    expect(descriptionOf(button)).not.toHaveClass("sr-only");
  });

  it("reveals the description on hover", () => {
    const { button } = renderButton();
    fireEvent.pointerEnter(button.parentElement!);
    expect(descriptionOf(button)).not.toHaveClass("sr-only");
    fireEvent.pointerLeave(button.parentElement!);
    expect(descriptionOf(button)).toHaveClass("sr-only");
  });

  it("aligns the revealed description to the left edge by default", () => {
    const { button } = renderButton();
    act(() => {
      button.focus();
    });
    expect(descriptionOf(button)).toHaveClass("left-0");
  });

  it("aligns the revealed description to the right edge when align is end", () => {
    const { button } = renderButton({ align: "end" });
    act(() => {
      button.focus();
    });
    expect(descriptionOf(button)).toHaveClass("right-0");
  });

  it("overrides the accessible name with ariaLabel while keeping the visible label text", () => {
    const { button } = renderButton({ ariaLabel: "Clear instruction" });
    expect(button).toHaveAccessibleName("Clear instruction");
    expect(button).toHaveTextContent("Archive");
  });

  it("falls back to the visible label as the accessible name when ariaLabel is omitted", () => {
    const { button } = renderButton();
    expect(button).not.toHaveAttribute("aria-label");
    expect(button).toHaveAccessibleName("Archive");
  });

  it("stays focusable and marks aria-busy while busy, and no-ops the click", async () => {
    const { button, onClick } = renderButton({ busy: true, label: "Archiving…" });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
    await userEvent.setup().click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("meets the 44px minimum target and carries the standard focus ring", () => {
    const { button } = renderButton();
    expect(button.className).toContain("min-h-11");
    expect(button.className).toContain("focus-visible:ring-2");
  });

  it("never carries the subdued opacity-70 treatment", () => {
    const { button } = renderButton({ variant: "destroy" });
    expect(button.className).not.toContain("opacity-70");
  });
});
