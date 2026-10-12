import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import WizardRail from "./WizardRail";

const STEPS = [
  { key: "identify", label: "Identify" },
  { key: "configure", label: "Configure" },
  { key: "dispatchable", label: "Make dispatchable" },
  { key: "posture", label: "Posture" },
] as const;

describe("WizardRail", () => {
  it("marks the current step with aria-current and an accessible '(current)' label", () => {
    render(<WizardRail steps={STEPS} currentKey="configure" />);
    const current = screen.getByTestId("wizard-rail-step-configure");
    expect(current).toHaveAttribute("aria-current", "step");
    expect(current).toHaveTextContent("(current)");
  });

  it("marks earlier steps done and later steps upcoming", () => {
    render(<WizardRail steps={STEPS} currentKey="dispatchable" />);
    expect(screen.getByTestId("wizard-rail-step-identify")).toHaveTextContent("(done)");
    expect(screen.getByTestId("wizard-rail-step-configure")).toHaveTextContent("(done)");
    expect(screen.getByTestId("wizard-rail-step-dispatchable")).toHaveTextContent("(current)");
    expect(screen.getByTestId("wizard-rail-step-posture")).toHaveTextContent("(upcoming)");
  });

  it("renders every step upcoming when currentKey matches none", () => {
    render(<WizardRail steps={STEPS} currentKey="unknown-step" />);
    for (const step of STEPS) {
      expect(screen.getByTestId(`wizard-rail-step-${step.key}`)).toHaveTextContent("(upcoming)");
    }
  });

  it("gives the rail an accessible list label", () => {
    render(<WizardRail steps={STEPS} currentKey="identify" />);
    expect(screen.getByRole("list", { name: "Connect repository steps" })).toBeInTheDocument();
  });
});
