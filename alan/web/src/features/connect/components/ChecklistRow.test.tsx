import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ChecklistRow from "./ChecklistRow";

describe("ChecklistRow", () => {
  it("renders the label and a textual pass status (color is never the only channel)", () => {
    render(<ChecklistRow label="Identity" status="pass" testId="row-identity" />);
    expect(screen.getByTestId("row-identity-label")).toHaveTextContent("Identity");
    expect(screen.getByTestId("row-identity-status")).toHaveTextContent("pass");
    expect(screen.getByTestId("row-identity")).not.toHaveAttribute("role");
  });

  it("renders role=alert and the fail label for a blocking status", () => {
    render(<ChecklistRow label="Credential" status="fail" testId="row-credential" />);
    expect(screen.getByTestId("row-credential")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("row-credential-status")).toHaveTextContent("fail");
  });

  it("renders 'no forge configured' copy for the no_forge status", () => {
    render(<ChecklistRow label="Reachability" status="no_forge" testId="row-reach" />);
    expect(screen.getByTestId("row-reach-status")).toHaveTextContent("no forge configured");
  });

  it("renders 'not yet checked' for the pending status", () => {
    render(<ChecklistRow label="Reachability" status="pending" testId="row-reach" />);
    expect(screen.getByTestId("row-reach-status")).toHaveTextContent("not yet checked");
  });

  it("renders the three additive step-3 statuses with the neutral pending chip, no role=alert", () => {
    const cases: { status: "not_started" | "waiting" | "unverifiable"; label: string }[] = [
      { status: "not_started", label: "not started" },
      { status: "waiting", label: "waiting on you" },
      { status: "unverifiable", label: "can't verify from here" },
    ];
    for (const { status, label } of cases) {
      const { unmount } = render(<ChecklistRow label="X" status={status} testId={`row-${status}`} />);
      expect(screen.getByTestId(`row-${status}-status`)).toHaveTextContent(label);
      expect(screen.getByTestId(`row-${status}`)).not.toHaveAttribute("role");
      unmount();
    }
  });

  it("renders optional detail, remedy, and action only when provided", () => {
    const { rerender } = render(<ChecklistRow label="X" status="unknown" testId="row-x" />);
    expect(screen.queryByTestId("row-x-detail")).not.toBeInTheDocument();
    expect(screen.queryByTestId("row-x-remedy")).not.toBeInTheDocument();

    rerender(
      <ChecklistRow
        label="X"
        status="unknown"
        detail="cannot verify yet"
        remedy="try again later"
        action={<button type="button">Retry</button>}
        testId="row-x"
      />,
    );
    expect(screen.getByTestId("row-x-detail")).toHaveTextContent("cannot verify yet");
    expect(screen.getByTestId("row-x-remedy")).toHaveTextContent("try again later");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
