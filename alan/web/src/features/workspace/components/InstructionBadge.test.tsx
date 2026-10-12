import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { DispatchInstructionsReadback } from "../../../api";
import {
  activateDisclosure,
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import InstructionBadge from "./InstructionBadge";

function getSummary(testId: string): HTMLElement {
  const summary = screen.getByTestId(testId).closest("summary");
  if (summary === null) throw new Error("summary element not found");
  return summary;
}

describe("InstructionBadge", () => {
  it("renders a muted 'no instructions' pill for status=none with no disclosure", () => {
    const readback: DispatchInstructionsReadback = { status: "none", text: null, origin: null };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const pill = screen.getByTestId("instruction-badge-inv_001");
    expect(pill).toHaveTextContent("no instructions");
    expect(pill.className).toContain("status-neutral");
    expect(screen.queryByTestId("instruction-badge-inv_001-details")).not.toBeInTheDocument();
  });

  it("renders a neutral 'instructions unknown' pill for status=unknown with no disclosure", () => {
    const readback: DispatchInstructionsReadback = {
      status: "unknown",
      text: null,
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const pill = screen.getByTestId("instruction-badge-inv_001");
    expect(pill).toHaveTextContent("instructions unknown");
    expect(pill.className).toContain("status-neutral");
    expect(screen.queryByTestId("instruction-badge-inv_001-details")).not.toBeInTheDocument();
  });

  it("renders an active-styled 'instructions attached' pill, collapsed by default", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "build the widget",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const pill = screen.getByTestId("instruction-badge-inv_001");
    expect(pill).toHaveTextContent("instructions attached");
    expect(pill.className).toContain("status-active");
    const details = screen.getByTestId("instruction-badge-inv_001-details");
    expect(details).not.toHaveAttribute("open");
  });

  it("shows ▸ beside the status pill while closed and ▾ while open", async () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "build the widget",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const details = screen.getByTestId("instruction-badge-inv_001-details");
    const summary = disclosureSummary(details);
    await expectDisclosureState(details, false);
    expect(summary.lastElementChild).toBe(screen.getByTestId("instruction-badge-inv_001"));

    activateDisclosure(details, "click");

    await expectDisclosureState(details, true);
  });

  it("opens and closes on click, Enter and Space and ignores other keys", async () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "build the widget",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const details = screen.getByTestId("instruction-badge-inv_001-details");
    expect(screen.getByTestId("instruction-badge-inv_001-text")).not.toBeVisible();
    await expectTogglesOnEveryActivation(details);
    activateDisclosure(details, "Space");
    await expectDisclosureState(details, true);
    expect(screen.getByTestId("instruction-badge-inv_001-text")).toBeVisible();
  });

  it("shows no marker on the graph-node form of the badge", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "build the widget",
      origin: null,
    };
    const { container } = render(
      <InstructionBadge invId="inv_001" readback={readback} showDetails={false} />,
    );
    expect(container.querySelector("summary")).toBeNull();
    expect(container).not.toHaveTextContent(/[▸▾]/);
  });

  it("shows no marker on a pill that has no text to disclose", () => {
    const readback: DispatchInstructionsReadback = { status: "none", text: null, origin: null };
    const { container } = render(<InstructionBadge invId="inv_001" readback={readback} />);
    expect(container).not.toHaveTextContent(/[▸▾]/);
  });

  it("expands to reveal the full recorded text on click, with no truncation", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "line one\nline two\nline three",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    fireEvent.click(screen.getByTestId("instruction-badge-inv_001"));
    const details = screen.getByTestId("instruction-badge-inv_001-details");
    expect(details).toHaveAttribute("open");
    const text = screen.getByTestId("instruction-badge-inv_001-text");
    expect(text).toHaveTextContent("line one line two line three");
    expect(text.className).toContain("max-h-64");
    expect(text.className).not.toMatch(/truncate|line-clamp/);
  });

  it("collapses again on a second click", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "build the widget",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const pill = screen.getByTestId("instruction-badge-inv_001");
    fireEvent.click(pill);
    expect(screen.getByTestId("instruction-badge-inv_001-details")).toHaveAttribute("open");
    fireEvent.click(pill);
    expect(screen.getByTestId("instruction-badge-inv_001-details")).not.toHaveAttribute("open");
  });

  it("renders an attention-styled 'instructions not delivered' pill with the fixed explanation when expanded", () => {
    const readback: DispatchInstructionsReadback = {
      status: "not_delivered",
      text: "merge it",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const pill = screen.getByTestId("instruction-badge-inv_001");
    expect(pill.className).toContain("status-attention");
    fireEvent.click(pill);
    expect(screen.getByTestId("instruction-badge-inv_001-not-delivered-note")).toHaveTextContent(
      "This team's worker prompt does not carry dispatch instructions.",
    );
    expect(screen.getByTestId("instruction-badge-inv_001-text")).toHaveTextContent("merge it");
  });

  it("renders a bare pill with no disclosure when showDetails is false, even with text present", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "build the widget",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} showDetails={false} />);
    expect(screen.getByTestId("instruction-badge-inv_001")).toHaveTextContent("instructions attached");
    expect(screen.queryByTestId("instruction-badge-inv_001-details")).not.toBeInTheDocument();
  });

  it("opens and closes the disclosure via keyboard activation on the summary", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "do the thing",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const summary = getSummary("instruction-badge-inv_001");
    const details = screen.getByTestId("instruction-badge-inv_001-details");
    expect(details).not.toHaveAttribute("open");

    fireEvent.keyDown(summary, { key: "Enter" });
    expect(details).toHaveAttribute("open");

    fireEvent.keyDown(summary, { key: " " });
    expect(details).not.toHaveAttribute("open");

    fireEvent.keyDown(summary, { key: "Tab" });
    expect(details).not.toHaveAttribute("open");
  });

  it("is reachable via Tab and toggles with the keyboard", async () => {
    const user = userEvent.setup();
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "do the thing",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    const summary = getSummary("instruction-badge-inv_001");
    summary.focus();
    expect(summary).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(screen.getByTestId("instruction-badge-inv_001-details")).toHaveAttribute("open");
  });

  it("shows no origin suffix when status=attached and origin=null", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "do the thing",
      origin: null,
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    fireEvent.click(screen.getByTestId("instruction-badge-inv_001"));
    expect(screen.queryByTestId("instruction-badge-inv_001-origin")).not.toBeInTheDocument();
  });

  it("shows a 'typed for this run' suffix when status=attached and origin=dispatch", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "do the thing",
      origin: "dispatch",
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    fireEvent.click(screen.getByTestId("instruction-badge-inv_001"));
    expect(screen.getByTestId("instruction-badge-inv_001-origin")).toHaveTextContent(
      "typed for this run",
    );
  });

  it("shows a 'persisted from a prior dispatch' suffix when status=attached and origin=inherited", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "do the thing",
      origin: "inherited",
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    fireEvent.click(screen.getByTestId("instruction-badge-inv_001"));
    expect(screen.getByTestId("instruction-badge-inv_001-origin")).toHaveTextContent(
      "persisted from a prior dispatch",
    );
  });

  it("shows a 'generated by the system' suffix when status=attached and origin=generated", () => {
    const readback: DispatchInstructionsReadback = {
      status: "attached",
      text: "do the thing",
      origin: "generated",
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    fireEvent.click(screen.getByTestId("instruction-badge-inv_001"));
    expect(screen.getByTestId("instruction-badge-inv_001-origin")).toHaveTextContent(
      "generated by the system",
    );
  });

  it("never shows an origin suffix for status=not_delivered even when origin is present", () => {
    const readback: DispatchInstructionsReadback = {
      status: "not_delivered",
      text: "merge it",
      origin: "generated",
    };
    render(<InstructionBadge invId="inv_001" readback={readback} />);
    fireEvent.click(screen.getByTestId("instruction-badge-inv_001"));
    expect(screen.queryByTestId("instruction-badge-inv_001-origin")).not.toBeInTheDocument();
  });
});
