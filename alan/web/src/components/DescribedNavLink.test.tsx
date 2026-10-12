import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type React from "react";

import DescribedNavLink, { NAV_LINK_CHROME_CLASS } from "./DescribedNavLink";

function renderLink(overrides: Partial<React.ComponentProps<typeof DescribedNavLink>> = {}) {
  return render(
    <MemoryRouter>
      <DescribedNavLink
        to="/health"
        ariaLabel="Health"
        testId="nav-link"
        description="Fleet health findings."
        {...overrides}
      >
        Health
      </DescribedNavLink>
    </MemoryRouter>,
  );
}

describe("DescribedNavLink", () => {
  it("renders the ratified nav-link chrome recipe", () => {
    renderLink();
    expect(screen.getByTestId("nav-link").className).toContain(NAV_LINK_CHROME_CLASS);
  });

  it("links to the given destination with the given accessible name", () => {
    renderLink();
    const link = screen.getByTestId("nav-link");
    expect(link).toHaveAttribute("href", "/health");
    expect(link).toHaveAccessibleName("Health");
  });

  it("reveals the description on hover and on keyboard focus, wired via aria-describedby", () => {
    renderLink();
    const link = screen.getByTestId("nav-link");
    const describedBy = link.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    const description = document.getElementById(describedBy ?? "");
    expect(description).toHaveTextContent("Fleet health findings.");
    expect(description).toHaveClass("sr-only");
    act(() => {
      link.focus();
    });
    expect(description).not.toHaveClass("sr-only");
  });

  it("suppresses the count pill at zero or when omitted", () => {
    renderLink();
    expect(screen.queryByTestId("nav-link-count")).not.toBeInTheDocument();
  });

  it("shows the count pill once positive", () => {
    renderLink({ count: 3 });
    expect(screen.getByTestId("nav-link-count")).toHaveTextContent("3");
  });

  it("renders optional leading content ahead of the label", () => {
    renderLink({ leading: <span data-testid="dot">•</span> });
    const link = screen.getByTestId("nav-link");
    const dot = screen.getByTestId("dot");
    expect(link).toContainElement(dot);
    expect(link.firstChild).toBe(dot);
  });

  it("positions the description at the left edge by default", () => {
    renderLink();
    act(() => {
      screen.getByTestId("nav-link").focus();
    });
    const describedBy = screen.getByTestId("nav-link").getAttribute("aria-describedby");
    expect(document.getElementById(describedBy ?? "")).toHaveClass("left-0");
  });

  it("positions the description at the right edge when align is 'end'", () => {
    renderLink({ align: "end" });
    act(() => {
      screen.getByTestId("nav-link").focus();
    });
    const describedBy = screen.getByTestId("nav-link").getAttribute("aria-describedby");
    expect(document.getElementById(describedBy ?? "")).toHaveClass("right-0");
  });
});
