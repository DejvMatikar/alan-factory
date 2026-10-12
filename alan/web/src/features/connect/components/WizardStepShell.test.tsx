import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import WizardStepShell from "./WizardStepShell";

describe("WizardStepShell", () => {
  it("renders the title, the rail slot, and the children content", () => {
    render(
      <WizardStepShell title="Identify repository" rail={<div data-testid="fake-rail" />}>
        <p>step content</p>
      </WizardStepShell>,
    );
    expect(screen.getByRole("heading", { name: "Identify repository" })).toBeInTheDocument();
    expect(screen.getByTestId("fake-rail")).toBeInTheDocument();
    expect(screen.getByText("step content")).toBeInTheDocument();
  });
});
