import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Assumption } from "../../../api";
import AssumptionsList from "./AssumptionsList";

const OPEN_ASSUMPTION: Assumption = {
  id: "asm-open",
  text: "Assumed the API is idempotent",
  status: "open",
};

const ACCEPTED_ASSUMPTION: Assumption = {
  id: "asm-accepted",
  text: "Assumed retries are safe",
  status: "accepted",
};

const CHALLENGED_ASSUMPTION: Assumption = {
  id: "asm-challenged",
  text: "Assumed no auth token is required",
  status: "challenged",
};

describe("AssumptionsList", () => {
  it("renders the list container with data-testid", () => {
    render(<AssumptionsList assumptions={[OPEN_ASSUMPTION]} />);
    expect(screen.getByTestId("assumptions-checklist")).toBeInTheDocument();
  });

  it("renders a row for each assumption", () => {
    render(
      <AssumptionsList
        assumptions={[OPEN_ASSUMPTION, ACCEPTED_ASSUMPTION, CHALLENGED_ASSUMPTION]}
      />,
    );
    expect(screen.getByTestId("assumption-asm-open")).toBeInTheDocument();
    expect(screen.getByTestId("assumption-asm-accepted")).toBeInTheDocument();
    expect(screen.getByTestId("assumption-asm-challenged")).toBeInTheDocument();
  });

  it("shows assumption text", () => {
    render(<AssumptionsList assumptions={[OPEN_ASSUMPTION]} />);
    expect(screen.getByText("Assumed the API is idempotent")).toBeInTheDocument();
  });

  it("renders no status chip for an open assumption", () => {
    render(<AssumptionsList assumptions={[OPEN_ASSUMPTION]} />);
    expect(screen.queryByLabelText("status: open")).toBeNull();
    expect(screen.getByTestId("assumption-asm-open").querySelectorAll("span")).toHaveLength(1);
  });

  it("shows the accepted status label", () => {
    render(<AssumptionsList assumptions={[ACCEPTED_ASSUMPTION]} />);
    expect(screen.getByLabelText("status: accepted")).toHaveTextContent("accepted");
  });

  it("shows the challenged status label", () => {
    render(<AssumptionsList assumptions={[CHALLENGED_ASSUMPTION]} />);
    expect(screen.getByLabelText("status: challenged")).toHaveTextContent("challenged");
  });

  it("renders the accepted badge with status tokens, not the checklist's raw green utility", () => {
    render(<AssumptionsList assumptions={[ACCEPTED_ASSUMPTION]} />);
    const badge = screen.getByLabelText("status: accepted");
    expect(badge.className).toContain("bg-status-success-tint/15");
    expect(badge.className).toContain("text-status-success-fg");
    expect(badge.className).not.toContain("text-green-400");
  });

  it("renders empty list without error", () => {
    render(<AssumptionsList assumptions={[]} />);
    expect(screen.getByTestId("assumptions-checklist")).toBeInTheDocument();
    expect(screen.queryByTestId(/^assumption-/)).toBeNull();
  });
});
