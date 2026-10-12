import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type React from "react";

import DescribedIconButton from "./DescribedIconButton";

function renderButton(overrides: Partial<React.ComponentProps<typeof DescribedIconButton>> = {}) {
  const onClick = vi.fn();
  render(
    <DescribedIconButton
      glyph="✕"
      label="Close panel"
      description="Close this panel"
      testId="close-button"
      onClick={onClick}
      {...overrides}
    />,
  );
  return { onClick, button: screen.getByTestId("close-button") };
}

function descriptionOf(button: HTMLElement): HTMLElement | null {
  return document.getElementById(button.getAttribute("aria-describedby") ?? "");
}

describe("DescribedIconButton", () => {
  it("names the control by its label, not its glyph", () => {
    const { button } = renderButton();
    expect(button).toHaveAccessibleName("Close panel");
    expect(button).toHaveTextContent("✕");
  });

  it("hides the glyph from assistive technology", () => {
    const { button } = renderButton();
    expect(button.querySelector("span[aria-hidden='true']")).toHaveTextContent("✕");
  });

  it("is a ghost icon-only button at the 44px minimum target by default", () => {
    const { button } = renderButton();
    expect(button).toHaveAttribute("data-variant", "ghost");
    expect(button).toHaveAttribute("data-icon-only", "true");
    expect(button.className).toContain("min-h-11");
    expect(button.className).toContain("min-w-11");
  });

  it("uses the compact 24px target when asked", () => {
    const { button } = renderButton({ density: "compact" });
    expect(button.className).toContain("min-h-6");
    expect(button.className).not.toContain("min-h-11");
  });

  it("calls onClick when clicked", async () => {
    const { button, onClick } = renderButton();
    await userEvent.setup().click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("wires the description through aria-describedby and keeps it screen-reader-only at rest", () => {
    const { button } = renderButton();
    const description = descriptionOf(button);
    expect(description).toHaveTextContent("Close this panel");
    expect(description).toHaveAttribute("role", "tooltip");
    expect(description).toHaveClass("sr-only");
    expect(button).toHaveAccessibleDescription("Close this panel");
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

  it("states neither expansion nor control by default", () => {
    const { button } = renderButton();
    expect(button).not.toHaveAttribute("aria-expanded");
    expect(button).not.toHaveAttribute("aria-controls");
  });

  it("states expansion and the controlled region when given", () => {
    const { button } = renderButton({ expanded: true, controls: "region-1" });
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveAttribute("aria-controls", "region-1");
  });

  it("appends a caller class to the button", () => {
    const { button } = renderButton({ className: "border-0" });
    expect(button).toHaveClass("border-0");
  });

  it("gives two instances distinct description ids", () => {
    render(
      <>
        <DescribedIconButton glyph="✕" label="A" description="a" testId="a" onClick={vi.fn()} />
        <DescribedIconButton glyph="✕" label="B" description="b" testId="b" onClick={vi.fn()} />
      </>,
    );
    const a = screen.getByTestId("a").getAttribute("aria-describedby");
    const b = screen.getByTestId("b").getAttribute("aria-describedby");
    expect(a).not.toBeNull();
    expect(a).not.toBe(b);
  });
});
