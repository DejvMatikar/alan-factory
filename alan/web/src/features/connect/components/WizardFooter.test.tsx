import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import WizardFooter from "./WizardFooter";

describe("WizardFooter", () => {
  it("renders exactly one primary action with its caption directly beneath it", () => {
    render(
      <WizardFooter
        primaryLabel="Connect repository"
        onPrimary={() => undefined}
        caption="Registers acme/widget with alan and starts a read-only scan."
        testId="step1-footer"
      />,
    );
    expect(screen.getByTestId("step1-footer-primary")).toHaveTextContent("Connect repository");
    expect(screen.getByTestId("step1-footer-caption")).toHaveTextContent(
      "Registers acme/widget with alan and starts a read-only scan.",
    );
  });

  it("invokes onPrimary when clicked", async () => {
    const onPrimary = vi.fn();
    render(
      <WizardFooter primaryLabel="Go" onPrimary={onPrimary} caption="c" testId="footer" />,
    );
    await userEvent.click(screen.getByTestId("footer-primary"));
    expect(onPrimary).toHaveBeenCalledOnce();
  });

  it("disables the primary action when primaryDisabled is true", () => {
    render(
      <WizardFooter
        primaryLabel="Go"
        onPrimary={() => undefined}
        primaryDisabled
        caption="c"
        testId="footer"
      />,
    );
    expect(screen.getByTestId("footer-primary")).toBeDisabled();
  });
});
