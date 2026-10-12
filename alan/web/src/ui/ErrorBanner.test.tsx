import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ErrorBanner from "./ErrorBanner";

describe("ErrorBanner", () => {
  it("renders as an alert with the given testid", () => {
    render(<ErrorBanner data-testid="banner">Something broke</ErrorBanner>);

    expect(screen.getByTestId("banner")).toHaveAttribute("role", "alert");
  });

  it("renders its children", () => {
    render(<ErrorBanner>Something broke</ErrorBanner>);

    expect(screen.getByText("Something broke")).toBeInTheDocument();
  });

  it("emits the byte-identical banner chrome string with no action", () => {
    render(<ErrorBanner data-testid="banner">Something broke</ErrorBanner>);

    expect(screen.getByTestId("banner").className).toBe(
      "rounded-md bg-banner-error-tint/15 px-3 py-2 text-xs text-banner-error-fg",
    );
  });

  it("emits the byte-identical banner chrome string with an action", () => {
    render(
      <ErrorBanner data-testid="banner" action={<button type="button">Retry</button>}>
        Something broke
      </ErrorBanner>,
    );

    expect(screen.getByTestId("banner").className).toBe(
      "flex items-center justify-between gap-3 rounded-md bg-banner-error-tint/15 px-3 py-2 text-xs text-banner-error-fg",
    );
  });

  it("wraps children in a span and adds flex chrome when action is present", () => {
    render(
      <ErrorBanner data-testid="banner" action={<button type="button">Retry</button>}>
        Something broke
      </ErrorBanner>,
    );

    const banner = screen.getByTestId("banner");
    expect(banner).toHaveClass("flex", "items-center", "justify-between", "gap-3");
    const span = banner.querySelector("span");
    expect(span).not.toBeNull();
    expect(span).toHaveTextContent("Something broke");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("does not wrap children in a span and omits flex chrome when action is undefined", () => {
    render(<ErrorBanner data-testid="banner">Something broke</ErrorBanner>);

    const banner = screen.getByTestId("banner");
    expect(banner).not.toHaveClass("flex");
    expect(banner.querySelector("span")).toBeNull();
  });

  it("keeps the flex chrome when action is explicitly null", () => {
    render(
      <ErrorBanner data-testid="banner" action={null}>
        Something broke
      </ErrorBanner>,
    );

    const banner = screen.getByTestId("banner");
    expect(banner).toHaveClass("flex", "items-center", "justify-between", "gap-3");
    expect(banner.querySelector("span")).not.toBeNull();
  });

  it("renders a present but empty banner for empty-string children", () => {
    render(<ErrorBanner data-testid="banner">{""}</ErrorBanner>);

    const banner = screen.getByTestId("banner");
    expect(banner).toBeInTheDocument();
    expect(banner).toBeEmptyDOMElement();
  });
});
