import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StepCardIcons } from "./NodeCardIcons";

function descriptionOf(button: HTMLElement): HTMLElement {
  return document.getElementById(button.getAttribute("aria-describedby")!)!;
}

describe("StepCardIcons", () => {
  it("renders Output, Prompt and Config as the shared icon-only Button", () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    for (const id of ["icon-output", "icon-prompt", "icon-config"]) {
      const button = screen.getByTestId(id);
      expect(button.tagName).toBe("BUTTON");
      expect(button).toHaveAttribute("data-icon-only", "true");
      expect(button).toHaveAttribute("type", "button");
    }
  });

  it("gives every icon a clickable area of at least 24x24 and never the 44px default", () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    for (const id of ["icon-output", "icon-prompt", "icon-config"]) {
      const { className } = screen.getByTestId(id);
      expect(className).toContain("min-h-6");
      expect(className).toContain("min-w-6");
      expect(className).not.toContain("min-h-11");
      expect(className).not.toContain("min-w-11");
    }
  });

  it("keeps a press on an icon from starting a graph drag or pan", () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    expect(screen.getByTestId("step-card-icons")).toHaveClass("nodrag", "nopan");
  });

  it("puts the step it belongs to in each accessible name", () => {
    render(<StepCardIcons label="reviewer (step_2)" onOpen={vi.fn()} />);
    expect(screen.getByRole("button", { name: "View output of reviewer (step_2)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View prompt of reviewer (step_2)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View config of reviewer (step_2)" })).toBeInTheDocument();
  });

  it("tells the same control on two different cards apart by name", () => {
    render(
      <>
        <StepCardIcons label="reviewer" onOpen={vi.fn()} />
        <StepCardIcons label="writer" onOpen={vi.fn()} />
      </>,
    );
    expect(screen.getAllByRole("button", { name: /^View output of / }).map((b) => b.getAttribute("aria-label"))).toEqual([
      "View output of reviewer",
      "View output of writer",
    ]);
  });

  it("carries no title attribute on any icon", () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    for (const button of screen.getAllByRole("button")) {
      expect(button).not.toHaveAttribute("title");
    }
  });

  it("keeps every description hidden until the icon is hovered or focused", () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    for (const button of screen.getAllByRole("button")) {
      expect(descriptionOf(button)).toHaveClass("sr-only");
    }
  });

  it("shows a plain-language description on mouse hover", async () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    const output = screen.getByTestId("icon-output");
    await userEvent.hover(output);
    const description = descriptionOf(output);
    expect(description).not.toHaveClass("sr-only");
    expect(description).toHaveTextContent("See what it produced");
    expect(description).toHaveAttribute("role", "tooltip");
  });

  it("shows a description on keyboard focus", async () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    await userEvent.tab();
    const output = screen.getByTestId("icon-output");
    expect(output).toHaveFocus();
    expect(descriptionOf(output)).not.toHaveClass("sr-only");
  });

  it("describes each icon in its own words", async () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    await userEvent.hover(screen.getByTestId("icon-prompt"));
    expect(descriptionOf(screen.getByTestId("icon-prompt"))).toHaveTextContent(
      "See the instructions used",
    );
    await userEvent.hover(screen.getByTestId("icon-config"));
    expect(descriptionOf(screen.getByTestId("icon-config"))).toHaveTextContent(
      "See the model and settings",
    );
  });

  it("anchors every description to its icon's right edge so it never runs past the card's right side", async () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    for (const id of ["icon-output", "icon-prompt", "icon-config"]) {
      await userEvent.hover(screen.getByTestId(id));
      const { className } = descriptionOf(screen.getByTestId(id));
      expect(className).toContain("right-0");
      expect(className).not.toContain("left-0");
    }
  });

  it("gives every description a distinct id, even across cards", () => {
    render(
      <>
        <StepCardIcons label="reviewer" onOpen={vi.fn()} />
        <StepCardIcons label="writer" onOpen={vi.fn()} />
      </>,
    );
    const ids = screen.getAllByRole("button").map((b) => b.getAttribute("aria-describedby"));
    expect(new Set(ids).size).toBe(6);
  });

  it("no longer draws the Prompt glyph as a question mark", () => {
    render(<StepCardIcons label="reviewer" onOpen={vi.fn()} />);
    const svg = screen.getByTestId("icon-prompt").querySelector("svg")!;
    const path = svg.querySelector("path")!.getAttribute("d")!;
    expect(path).not.toContain("M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Z");
    expect(path.startsWith("M10 2c-2.236")).toBe(true);
    expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  it.each([
    ["icon-output", "output"],
    ["icon-prompt", "prompt"],
    ["icon-config", "config"],
  ] as const)("opens the %s tab on click", async (testId, tab) => {
    const onOpen = vi.fn();
    render(<StepCardIcons label="reviewer" onOpen={onOpen} />);
    await userEvent.click(screen.getByTestId(testId));
    expect(onOpen).toHaveBeenCalledExactlyOnceWith(tab);
  });

  it("stops the click from reaching a parent handler", async () => {
    const parentClick = vi.fn();
    render(
      <div role="presentation" onClick={parentClick}>
        <StepCardIcons label="reviewer" onOpen={vi.fn()} />
      </div>,
    );
    await userEvent.click(screen.getByTestId("icon-output"));
    expect(parentClick).not.toHaveBeenCalled();
  });
});
