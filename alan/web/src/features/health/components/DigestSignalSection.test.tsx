import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DigestSignalSection from "./DigestSignalSection";

describe("DigestSignalSection", () => {
  it("renders an explicit not-measured-yet state, not a zero, when recovery is null", () => {
    render(<DigestSignalSection recovery={null} residue={null} digestOverdue={false} />);
    expect(screen.getByTestId("metric-digest-recovery")).toHaveTextContent("not measured yet");
    expect(screen.queryByTestId("metric-digest-recovery-trend")).not.toBeInTheDocument();
  });

  it("renders the current count and an 'up' word alongside a glyph", () => {
    render(
      <DigestSignalSection
        recovery={{ current_count: 5, previous_count: 2, direction: "up" }}
        residue={null}
        digestOverdue={false}
      />,
    );
    expect(screen.getByTestId("metric-digest-recovery")).toHaveTextContent("5");
    expect(screen.getByTestId("metric-digest-recovery-trend")).toHaveTextContent("up");
  });

  it("renders 'steady' and 'down' directions in plain text", () => {
    const { rerender } = render(
      <DigestSignalSection
        recovery={{ current_count: 5, previous_count: 5, direction: "same" }}
        residue={null}
        digestOverdue={false}
      />,
    );
    expect(screen.getByTestId("metric-digest-recovery-trend")).toHaveTextContent("steady");
    rerender(
      <DigestSignalSection
        recovery={{ current_count: 2, previous_count: 5, direction: "down" }}
        residue={null}
        digestOverdue={false}
      />,
    );
    expect(screen.getByTestId("metric-digest-recovery-trend")).toHaveTextContent("down");
  });

  it("associates the explainer with the value via aria-describedby", () => {
    render(<DigestSignalSection recovery={null} residue={null} digestOverdue={false} />);
    const value = screen.getByTestId("metric-digest-recovery");
    const describedBy = value.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(/recovery events/i);
  });

  it("shows an overdue chip when the digest sweep is overdue, and an on-time chip otherwise", () => {
    const { rerender } = render(
      <DigestSignalSection recovery={null} residue={null} digestOverdue={true} />,
    );
    expect(screen.getByTestId("aggregate-chip-digest-sweep")).toHaveTextContent("Overdue");
    rerender(<DigestSignalSection recovery={null} residue={null} digestOverdue={false} />);
    expect(screen.getByTestId("aggregate-chip-digest-sweep")).toHaveTextContent("On time");
  });

  it("shows an unknown chip, not an on-time claim, when digestOverdue has not been established yet", () => {
    render(<DigestSignalSection recovery={null} residue={null} digestOverdue={null} />);
    const chip = screen.getByTestId("aggregate-chip-digest-sweep");
    expect(chip).toHaveTextContent("Unknown");
    expect(chip).not.toHaveTextContent("On time");
  });

  it("renders an explicit not-measured-yet state for residue, not a zero, when residue is null", () => {
    render(<DigestSignalSection recovery={null} residue={null} digestOverdue={false} />);
    expect(screen.getByTestId("metric-digest-residue")).toHaveTextContent("not measured yet");
  });

  it("renders zero distinctly from not-measured-yet when the window is clean", () => {
    render(<DigestSignalSection recovery={null} residue={[]} digestOverdue={false} />);
    expect(screen.getByTestId("metric-digest-residue")).toHaveTextContent("0");
    expect(screen.getByTestId("metric-digest-residue")).not.toHaveTextContent("not measured yet");
  });

  it("renders the residue count when entries are present", () => {
    render(
      <DigestSignalSection
        recovery={null}
        residue={[
          { fingerprint: "fp-1", title: "Unclassified anomaly one" },
          { fingerprint: "fp-2", title: "Unclassified anomaly two" },
        ]}
        digestOverdue={false}
      />,
    );
    expect(screen.getByTestId("metric-digest-residue")).toHaveTextContent("2");
  });
});
