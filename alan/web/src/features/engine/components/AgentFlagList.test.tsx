import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeFlag } from "../../../test/agentFixtures";
import AgentFlagList from "./AgentFlagList";

describe("AgentFlagList", () => {
  it("renders nothing for an empty list", () => {
    const { container } = render(<AgentFlagList flags={[]} testId="flags" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows each flag's severity word, title, code and detail", () => {
    render(
      <AgentFlagList
        testId="flags"
        flags={[
          makeFlag(),
          makeFlag({
            code: "legacy_planning_endpoint",
            severity: "info",
            title: "Legacy planning endpoint",
            detail: "No caller in the app.",
          }),
        ]}
      />,
    );
    const warn = screen.getByTestId("flags-ignores_engine_roles");
    expect(warn).toHaveTextContent("Warning");
    expect(warn).toHaveTextContent("Ignores the engine's roles");
    expect(warn).toHaveTextContent("ignores_engine_roles");
    expect(warn).toHaveTextContent("The model is built in");
    const info = screen.getByTestId("flags-legacy_planning_endpoint");
    expect(info).toHaveTextContent("Note");
    expect(info).toHaveTextContent("No caller in the app.");
  });

  it("is a labelled list so assistive technology can find the flagged problems", () => {
    render(<AgentFlagList testId="flags" flags={[makeFlag()]} />);
    expect(screen.getByRole("list", { name: "Flagged configuration problems" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });
});
