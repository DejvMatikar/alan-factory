import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SectionHeading from "./SectionHeading";

const LEVELS = ["h2", "h3", "h4", "h5"] as const;

describe("SectionHeading", () => {
  it.each(LEVELS)("renders a real <%s> tag", (level) => {
    render(
      <SectionHeading level={level} data-testid="heading">
        Title
      </SectionHeading>,
    );

    expect(screen.getByTestId("heading").tagName).toBe(level.toUpperCase());
  });

  it("emits the byte-identical heading chrome string without truncate", () => {
    render(
      <SectionHeading level="h3" data-testid="heading">
        Title
      </SectionHeading>,
    );

    expect(screen.getByTestId("heading").className).toBe(
      "text-xs font-semibold uppercase tracking-wide text-text-muted",
    );
  });

  it("emits the byte-identical heading chrome string with truncate", () => {
    render(
      <SectionHeading level="h4" truncate data-testid="heading">
        Title
      </SectionHeading>,
    );

    expect(screen.getByTestId("heading").className).toBe(
      "min-w-0 truncate text-xs font-semibold uppercase tracking-wide text-text-muted",
    );
  });

  it("includes the truncate chrome when truncate is true", () => {
    render(
      <SectionHeading level="h4" truncate data-testid="heading">
        Title
      </SectionHeading>,
    );

    expect(screen.getByTestId("heading")).toHaveClass("min-w-0", "truncate");
  });

  it("excludes the truncate chrome when truncate is absent", () => {
    render(
      <SectionHeading level="h4" data-testid="heading">
        Title
      </SectionHeading>,
    );

    const heading = screen.getByTestId("heading");
    expect(heading).not.toHaveClass("min-w-0");
    expect(heading).not.toHaveClass("truncate");
  });

  it("passes the testid through", () => {
    render(
      <SectionHeading level="h3" data-testid="section-heading-testid">
        Title
      </SectionHeading>,
    );

    expect(screen.getByTestId("section-heading-testid")).toBeInTheDocument();
  });
});
