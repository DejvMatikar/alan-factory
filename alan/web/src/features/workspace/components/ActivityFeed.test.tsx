import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorkspaceNotice } from "../hooks/workspaceNotices";
import ActivityFeed from "./ActivityFeed";

const ERROR_NOTICE: WorkspaceNotice = {
  id: "n-1",
  timestamp: "2026-06-09T10:00:00.000Z",
  text: "Job failed",
  severity: "error",
};

const WARN_NOTICE: WorkspaceNotice = {
  id: "n-2",
  timestamp: "2026-06-09T10:01:00.000Z",
  text: "Job retrying after failure",
  severity: "warn",
};

const INFO_NOTICE: WorkspaceNotice = {
  id: "n-3",
  timestamp: "2026-06-09T10:02:00.000Z",
  text: "Job completed",
  severity: "info",
};

describe("ActivityFeed", () => {
  it("renders nothing when notices is empty", () => {
    const { container } = render(<ActivityFeed notices={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the activity-feed list when notices are present", () => {
    render(<ActivityFeed notices={[ERROR_NOTICE]} />);
    expect(screen.getByTestId("activity-feed")).toBeInTheDocument();
  });

  it("renders each notice as a list item", () => {
    render(<ActivityFeed notices={[ERROR_NOTICE, WARN_NOTICE, INFO_NOTICE]} />);
    const entries = screen.getAllByTestId(/^activity-entry-/);
    expect(entries).toHaveLength(3);
  });

  it("shows notice text in the entry", () => {
    render(<ActivityFeed notices={[ERROR_NOTICE]} />);
    expect(screen.getByText("Job failed")).toBeInTheDocument();
  });

  it("applies error style for error severity", () => {
    render(<ActivityFeed notices={[ERROR_NOTICE]} />);
    const entry = screen.getByTestId("activity-entry-n-1");
    expect(entry.className).toContain("text-red-300");
  });

  it("applies warn style for warn severity", () => {
    render(<ActivityFeed notices={[WARN_NOTICE]} />);
    const entry = screen.getByTestId("activity-entry-n-2");
    expect(entry.className).toContain("text-amber-300");
  });

  it("applies muted style for info severity", () => {
    render(<ActivityFeed notices={[INFO_NOTICE]} />);
    const entry = screen.getByTestId("activity-entry-n-3");
    expect(entry.className).toContain("text-text-muted");
  });

  it("renders a time element with the ISO timestamp as dateTime attribute", () => {
    render(<ActivityFeed notices={[ERROR_NOTICE]} />);
    const timeEl = screen.getByRole("time");
    expect(timeEl.getAttribute("dateTime")).toBe("2026-06-09T10:00:00.000Z");
  });

  it("multiple notices with different ids render as distinct keys (no duplicate keys warning)", () => {
    render(<ActivityFeed notices={[ERROR_NOTICE, WARN_NOTICE]} />);
    expect(screen.getAllByTestId(/^activity-entry-/)).toHaveLength(2);
  });
});
