import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AutoArchiveBadge from "./AutoArchiveBadge";

describe("AutoArchiveBadge", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-02T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the countdown text for an eligible, idle workspace", () => {
    render(<AutoArchiveBadge taskId="w1" updatedAt="2026-05-21T12:00:00Z" days={14} />);
    expect(screen.getByTestId("auto-archive-badge-w1")).toHaveTextContent("auto-archives in 2 days");
  });

  it("renders nothing when the countdown helper returns null", () => {
    const { container } = render(<AutoArchiveBadge taskId="w2" updatedAt="not-a-date" days={14} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("exposes a hover title and a keyboard-reachable aria-describedby explainer", () => {
    render(<AutoArchiveBadge taskId="w3" updatedAt="2026-05-21T12:00:00Z" days={14} />);
    const badge = screen.getByTestId("auto-archive-badge-w3");
    expect(badge).toHaveAttribute("tabIndex", "0");
    expect(badge).toHaveAttribute("title");
    expect(badge.getAttribute("title")).toMatch(/approximate/i);
    const describedBy = badge.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(/approximate/i);
  });

  it("uses the warning tint recipe, not DependencySweepBadge's un-ratified blue fill", () => {
    render(<AutoArchiveBadge taskId="w4" updatedAt="2026-05-21T12:00:00Z" days={14} />);
    const badge = screen.getByTestId("auto-archive-badge-w4");
    expect(badge.className).toContain("bg-status-warning-tint/15");
    expect(badge.className).toContain("text-status-warning-fg");
    expect(badge.className).not.toContain("rounded-full");
    expect(badge.className).not.toContain("blue");
  });

  it("carries no interactive role — it is a focusable label, not a control", () => {
    render(<AutoArchiveBadge taskId="w5" updatedAt="2026-05-21T12:00:00Z" days={14} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByTestId("auto-archive-badge-w5").tagName).toBe("SPAN");
  });
});
