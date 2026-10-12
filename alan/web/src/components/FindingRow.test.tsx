import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { EnginePreflightFinding } from "../api";
import FindingRow from "./FindingRow";

function finding(overrides: Partial<EnginePreflightFinding> = {}): EnginePreflightFinding {
  return {
    check: "some_check",
    status: "fail",
    message: "something is wrong",
    remedy: "fix the thing",
    remedy_url: "",
    ...overrides,
  };
}

describe("FindingRow", () => {
  it("renders a blocking finding with role=alert and the message/remedy text", () => {
    render(<FindingRow finding={finding()} testId="row" />);
    const container = screen.getByTestId("row");
    expect(container).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("row-message")).toHaveTextContent("something is wrong");
    expect(screen.getByTestId("row-remedy")).toHaveTextContent("fix the thing");
  });

  it("renders an unknown finding without role=alert", () => {
    render(<FindingRow finding={finding({ status: "unknown" })} testId="row" />);
    expect(screen.getByTestId("row")).not.toHaveAttribute("role");
  });

  it("renders a passing finding without role=alert", () => {
    render(<FindingRow finding={finding({ status: "pass", remedy: "" })} testId="row" />);
    expect(screen.getByTestId("row")).not.toHaveAttribute("role");
  });

  it("omits the remedy paragraph when remedy is empty", () => {
    render(<FindingRow finding={finding({ remedy: "" })} testId="row" />);
    expect(screen.queryByTestId("row-remedy")).not.toBeInTheDocument();
  });

  it("omits the link when remedy_url is an empty string", () => {
    render(<FindingRow finding={finding({ remedy_url: "" })} testId="row" />);
    expect(screen.queryByTestId("row-link")).not.toBeInTheDocument();
  });

  it("renders an actionable link labelled with the remedy text when remedy_url is present", () => {
    render(
      <FindingRow
        finding={finding({ remedy_url: "https://github.com/acme/widget/pull/7" })}
        testId="row"
      />,
    );
    const link = screen.getByTestId("row-link");
    expect(link).toHaveAttribute("href", "https://github.com/acme/widget/pull/7");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("aria-label", "fix the thing");
  });

  it("labels the link with the finding message when remedy is empty", () => {
    render(
      <FindingRow
        finding={finding({
          remedy: "",
          remedy_url: "https://github.com/acme/widget/settings/branches",
        })}
        testId="row"
      />,
    );
    expect(screen.getByTestId("row-link")).toHaveAttribute("aria-label", "something is wrong");
  });
});
