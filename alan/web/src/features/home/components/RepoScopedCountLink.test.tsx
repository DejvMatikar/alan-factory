import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import RepoScopedCountLink, { type RepoScopedCountLinkProps } from "./RepoScopedCountLink";

function renderLink(overrides: Partial<RepoScopedCountLinkProps> = {}) {
  const props: RepoScopedCountLinkProps = {
    label: "Health",
    to: "/health?engine=acme%2Fwidget",
    count: 0,
    ariaLabel: "Health for acme/widget",
    testId: "repo-health-acme/widget",
    description: "acme/widget's health findings.",
    ...overrides,
  };
  return render(
    <MemoryRouter>
      <RepoScopedCountLink {...props} />
    </MemoryRouter>,
  );
}

describe("RepoScopedCountLink", () => {
  it("renders the label and links to the given scoped destination", () => {
    renderLink();
    const link = screen.getByTestId("repo-health-acme/widget");
    expect(link).toHaveTextContent("Health");
    expect(link).toHaveAttribute("href", "/health?engine=acme%2Fwidget");
  });

  it("carries the given accessible name, distinguishing it from a same-labelled global control", () => {
    renderLink({ ariaLabel: "Health for acme/widget" });
    expect(screen.getByTestId("repo-health-acme/widget")).toHaveAccessibleName(
      "Health for acme/widget",
    );
  });

  it("suppresses the count pill at zero", () => {
    renderLink({ count: 0 });
    expect(screen.queryByTestId("repo-health-acme/widget-count")).not.toBeInTheDocument();
  });

  it("renders the count pill using the shared nav-count-pill class when the count is positive", () => {
    renderLink({ count: 3 });
    const pill = screen.getByTestId("repo-health-acme/widget-count");
    expect(pill).toHaveTextContent("3");
    expect(pill.className).toContain("rounded-full");
  });

  it("reuses the ratified nav-link chrome recipe", () => {
    renderLink();
    const link = screen.getByTestId("repo-health-acme/widget");
    expect(link.className).toContain(
      "inline-flex min-h-11 min-w-11 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-text-primary hover:bg-surface-elevated",
    );
  });

  it("carries a hover/focus-revealed plain-language description of what the control does", () => {
    renderLink();
    const link = screen.getByTestId("repo-health-acme/widget");
    const describedBy = link.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "acme/widget's health findings.",
    );
  });
});
