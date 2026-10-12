import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  DISCLOSURE_ACTIVATIONS,
  activateDisclosure,
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
  settleDisclosure,
} from "../test/disclosure";
import Disclosure, { type DisclosurePreset } from "./Disclosure";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60";

interface ParityCase {
  readonly preset: DisclosurePreset;
  readonly legacy: string;
  readonly added: string;
}

const PANEL_LEGACY =
  "flex min-h-11 w-fit cursor-pointer list-none items-center gap-2 text-left text-sm font-medium text-text-muted hover:text-text-primary [&::-webkit-details-marker]:hidden";

const ROW_MARKER_LAYOUT = "flex list-none items-baseline gap-1 [&::-webkit-details-marker]:hidden";

const PARITY_CASES: readonly ParityCase[] = [
  { preset: "panel", legacy: PANEL_LEGACY, added: "" },
  {
    preset: "panelXs",
    legacy: PANEL_LEGACY.replace("text-sm", "text-xs"),
    added: "",
  },
  {
    preset: "markdown",
    legacy:
      "flex min-h-11 cursor-pointer list-none items-center gap-2 text-sm font-medium text-text-primary [&::-webkit-details-marker]:hidden",
    added: "",
  },
  {
    preset: "group",
    legacy:
      "flex cursor-pointer list-none items-center gap-2 text-sm font-medium [&::-webkit-details-marker]:hidden",
    added: "",
  },
  {
    preset: "badge",
    legacy: "inline-flex cursor-pointer list-none [&::-webkit-details-marker]:hidden",
    added: "items-center gap-1",
  },
  {
    preset: "row",
    legacy: "cursor-pointer font-medium text-text-primary",
    added: ROW_MARKER_LAYOUT,
  },
  {
    preset: "rowMuted",
    legacy: "cursor-pointer text-xs text-text-muted",
    added: ROW_MARKER_LAYOUT,
  },
];

function renderDisclosure(preset: DisclosurePreset = "panel", defaultOpen = false) {
  render(
    <Disclosure preset={preset} defaultOpen={defaultOpen} testId="disc" label="Details">
      <p>Body text</p>
    </Disclosure>,
  );
  return screen.getByTestId("disc");
}

function ControlledHarness({ onChange }: Readonly<{ onChange: (open: boolean) => void }>) {
  const [open, setOpen] = useState(false);
  return (
    <Disclosure
      preset="panel"
      defaultOpen={false}
      open={open}
      onOpenChange={(next) => {
        onChange(next);
        setOpen(next);
      }}
      testId="disc"
      label="Controlled"
    >
      <p>Body text</p>
    </Disclosure>
  );
}

describe("Disclosure", () => {
  it.each(PARITY_CASES)(
    "renders the $preset preset with today's summary classes plus only the standard focus ring and marker layout",
    ({ preset, legacy, added }) => {
      const summary = disclosureSummary(renderDisclosure(preset));
      const expected = [legacy, added, FOCUS_RING].filter((part) => part !== "").join(" ");
      expect(summary.className).toBe(expected);
    },
  );

  it("shows ▸ when defaultOpen is false and keeps the body in the DOM", async () => {
    const details = renderDisclosure("panel", false);
    await expectDisclosureState(details, false);
    expect(disclosureSummary(details)).toHaveTextContent("Details");
    expect(screen.getByText("Body text")).toBeInTheDocument();
  });

  it("shows ▾ and an open body when defaultOpen is true", async () => {
    const details = renderDisclosure("panel", true);
    await expectDisclosureState(details, true);
    expect(screen.getByText("Body text")).toBeVisible();
  });

  it("places the marker before the label and hides it from assistive technology", () => {
    const summary = disclosureSummary(renderDisclosure());
    const marker = summary.firstElementChild;
    expect(marker).toHaveAttribute("aria-hidden", "true");
    expect(marker?.nextSibling?.textContent).toBe("Details");
  });

  it("never sets aria-expanded on the summary", () => {
    expect(disclosureSummary(renderDisclosure())).not.toHaveAttribute("aria-expanded");
  });

  it("opens and closes on click, Enter and Space and ignores other keys when closed by default", async () => {
    await expectTogglesOnEveryActivation(renderDisclosure("panel", false));
  });

  it("opens and closes on click, Enter and Space and ignores other keys when open by default", async () => {
    await expectTogglesOnEveryActivation(renderDisclosure("panel", true));
  });

  it("reports each activation to onOpenChange exactly once", async () => {
    const onOpenChange = vi.fn();
    render(
      <Disclosure preset="row" defaultOpen={false} onOpenChange={onOpenChange} testId="disc" label="Row">
        <p>Body text</p>
      </Disclosure>,
    );
    const details = screen.getByTestId("disc");
    for (const activation of DISCLOSURE_ACTIVATIONS) {
      onOpenChange.mockClear();
      activateDisclosure(details, activation);
      await settleDisclosure();
      expect(onOpenChange).toHaveBeenCalledTimes(1);
      expect(onOpenChange).toHaveBeenLastCalledWith(true);
      onOpenChange.mockClear();
      activateDisclosure(details, activation);
      await settleDisclosure();
      expect(onOpenChange).toHaveBeenCalledTimes(1);
      expect(onOpenChange).toHaveBeenLastCalledWith(false);
    }
  });

  it("does not call onOpenChange for a key that is not an activation key", async () => {
    const onOpenChange = vi.fn();
    render(
      <Disclosure preset="row" defaultOpen={false} onOpenChange={onOpenChange} testId="disc" label="Row" />,
    );
    const details = screen.getByTestId("disc");
    activateDisclosure(details, "click");
    await settleDisclosure();
    onOpenChange.mockClear();
    fireEvent.keyDown(disclosureSummary(details), { key: "Tab" });
    await settleDisclosure();
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("follows the open prop when controlled, whatever defaultOpen says", async () => {
    render(
      <Disclosure preset="panel" defaultOpen={false} open testId="disc" label="Controlled">
        <p>Body text</p>
      </Disclosure>,
    );
    await expectDisclosureState(screen.getByTestId("disc"), true);
  });

  it("lets a parent that owns the state drive it through onOpenChange on every activation", async () => {
    const onChange = vi.fn();
    render(<ControlledHarness onChange={onChange} />);
    const details = screen.getByTestId("disc");
    await expectTogglesOnEveryActivation(details);
    expect(onChange).toHaveBeenCalledTimes(DISCLOSURE_ACTIVATIONS.length * 2);
  });

  it("forwards the details className and both test ids", () => {
    render(
      <Disclosure
        preset="badge"
        defaultOpen={false}
        className="mt-2"
        testId="disc"
        summaryTestId="disc-summary"
        label="Label"
      />,
    );
    expect(screen.getByTestId("disc")).toHaveClass("mt-2");
    expect(screen.getByTestId("disc-summary").tagName).toBe("SUMMARY");
  });

  it("renders a node label as-is after the marker", () => {
    render(
      <Disclosure preset="group" defaultOpen testId="disc" label={<span data-testid="node-label">Node</span>} />,
    );
    const summary = disclosureSummary(screen.getByTestId("disc"));
    expect(summary.lastElementChild).toBe(screen.getByTestId("node-label"));
  });
});
