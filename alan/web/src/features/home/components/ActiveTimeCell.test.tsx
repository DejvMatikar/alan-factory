import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ActiveTimeCell from "./ActiveTimeCell";

describe("ActiveTimeCell — value variant", () => {
  it("renders the compact formatted duration with no Σ prefix", () => {
    render(<ActiveTimeCell seconds={7800} variant="value" testId="active-value" />);
    const cell = screen.getByTestId("active-value");
    expect(cell).toHaveTextContent("2h 10m");
    expect(cell.querySelector('[aria-hidden="true"]')).toBeNull();
  });

  it("states the precise value and the waiting exclusion in the aria-label", () => {
    render(<ActiveTimeCell seconds={7800} variant="value" testId="active-value" />);
    expect(screen.getByTestId("active-value")).toHaveAccessibleName(
      "Active fleet time 2h 10m, excludes time waiting on a human, external checks, capacity, or idle time",
    );
  });

  it("renders an explicit empty state and a not-recorded aria-label for a null value", () => {
    render(<ActiveTimeCell seconds={null} variant="value" testId="active-empty" />);
    const cell = screen.getByTestId("active-empty");
    expect(cell).toHaveTextContent("—");
    expect(cell).toHaveAccessibleName(
      "Active fleet time not recorded, excludes time waiting on a human, external checks, capacity, or idle time",
    );
  });
});

describe("ActiveTimeCell — sum variant", () => {
  it("renders a visible aria-hidden Σ prefix ahead of the compact duration", () => {
    render(<ActiveTimeCell seconds={9000} variant="sum" testId="active-sum" />);
    const cell = screen.getByTestId("active-sum");
    expect(cell).toHaveTextContent("Σ 2h 30m");
    const prefix = cell.querySelector('[aria-hidden="true"]');
    expect(prefix).not.toBeNull();
    expect(prefix).toHaveTextContent("Σ");
  });

  it("uses a sum-flavoured aria-label distinguishing effort from elapsed time", () => {
    render(<ActiveTimeCell seconds={9000} variant="sum" testId="active-sum" />);
    const label = screen.getByTestId("active-sum").getAttribute("aria-label");
    expect(label).toContain("Sum of active fleet time 2h 30m");
    expect(label).toContain("not elapsed calendar time");
    expect(label).toContain("excludes time waiting");
  });

  it("renders an explicit empty state and a not-recorded sum aria-label when no child has data", () => {
    render(<ActiveTimeCell seconds={null} variant="sum" testId="active-sum-empty" />);
    const cell = screen.getByTestId("active-sum-empty");
    expect(cell).toHaveTextContent("Σ —");
    expect(cell).toHaveAccessibleName(
      "Sum of active fleet time not recorded, excludes time waiting on a human, external checks, capacity, or idle time",
    );
  });
});
