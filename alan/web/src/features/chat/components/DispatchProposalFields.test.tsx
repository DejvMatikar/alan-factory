import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CounterCase, DispatchProposal } from "../../../api";
import DispatchProposalFields, { COUNTER_CASE_ID, counterCaseDescribedBy } from "./DispatchProposalFields";

const COUNTER_CASE: CounterCase = {
  against: "The scope is still ambiguous.",
  cheapest_80: "Ship the read path only.",
  cost_estimate: "insufficient data",
};

const BASE_PROPOSAL: DispatchProposal = {
  team: "implementation_plan",
  rationale: "spec is unclear; plan first",
  instructions: null,
  scope_audit: [],
  counter_case: COUNTER_CASE,
};

describe("DispatchProposalFields", () => {
  it("renders the rationale", () => {
    render(<DispatchProposalFields proposal={BASE_PROPOSAL} />);
    expect(screen.getByTestId("dispatch-proposal-rationale")).toHaveTextContent(
      "spec is unclear",
    );
  });

  it("omits the title section when the proposal carries none", () => {
    render(<DispatchProposalFields proposal={BASE_PROPOSAL} />);
    expect(screen.queryByTestId("dispatch-proposal-title")).toBeNull();
  });

  it("renders the title when the proposal carries one", () => {
    render(<DispatchProposalFields proposal={{ ...BASE_PROPOSAL, title: "add-thing" }} />);
    expect(screen.getByTestId("dispatch-proposal-title")).toHaveTextContent("add-thing");
  });

  it("omits the acceptance-clauses list when there are none", () => {
    render(<DispatchProposalFields proposal={BASE_PROPOSAL} />);
    expect(screen.queryByTestId("dispatch-proposal-acceptance-clauses")).toBeNull();
  });

  it("omits the acceptance-clauses list when the array is empty", () => {
    render(<DispatchProposalFields proposal={{ ...BASE_PROPOSAL, acceptance_clauses: [] }} />);
    expect(screen.queryByTestId("dispatch-proposal-acceptance-clauses")).toBeNull();
  });

  it("renders each acceptance clause when present", () => {
    render(
      <DispatchProposalFields
        proposal={{ ...BASE_PROPOSAL, acceptance_clauses: ["clause one", "clause two"] }}
      />,
    );
    const list = screen.getByTestId("dispatch-proposal-acceptance-clauses");
    expect(list).toHaveTextContent("clause one");
    expect(list).toHaveTextContent("clause two");
  });

  it("states the blocking count in the heading text above the blocking list", () => {
    render(
      <DispatchProposalFields
        proposal={{ ...BASE_PROPOSAL, acceptance_clauses: ["clause one", "clause two"] }}
      />,
    );
    expect(screen.getByText("Blocking (2)")).toBeInTheDocument();
  });

  it("omits the recorded group and its testid when recorded_clauses is empty", () => {
    render(
      <DispatchProposalFields
        proposal={{ ...BASE_PROPOSAL, acceptance_clauses: ["clause one"], recorded_clauses: [] }}
      />,
    );
    expect(screen.queryByTestId("dispatch-proposal-recorded-clauses")).toBeNull();
    expect(screen.queryByText(/^Recorded/)).toBeNull();
  });

  it("omits the recorded group and its testid when recorded_clauses is undefined", () => {
    render(
      <DispatchProposalFields proposal={{ ...BASE_PROPOSAL, acceptance_clauses: ["clause one"] }} />,
    );
    expect(screen.queryByTestId("dispatch-proposal-recorded-clauses")).toBeNull();
  });

  it("renders a recorded group with its count and items beside the blocking group", () => {
    render(
      <DispatchProposalFields
        proposal={{
          ...BASE_PROPOSAL,
          acceptance_clauses: ["clause one"],
          recorded_clauses: ["the docs say X", "the docs say Y"],
        }}
      />,
    );
    expect(screen.getByText("Blocking (1)")).toBeInTheDocument();
    expect(screen.getByText("Recorded (2)")).toBeInTheDocument();
    const recorded = screen.getByTestId("dispatch-proposal-recorded-clauses");
    expect(recorded).toHaveTextContent("the docs say X");
    expect(recorded).toHaveTextContent("the docs say Y");
    expect(screen.getByTestId("dispatch-proposal-acceptance-clauses")).not.toHaveTextContent(
      "the docs say X",
    );
  });

  it("shows Blocking (0) with no blocking list when every clause is recorded", () => {
    render(
      <DispatchProposalFields
        proposal={{ ...BASE_PROPOSAL, acceptance_clauses: [], recorded_clauses: ["the docs say X"] }}
      />,
    );
    expect(screen.getByText("Blocking (0)")).toBeInTheDocument();
    expect(screen.queryByTestId("dispatch-proposal-acceptance-clauses")).toBeNull();
    expect(screen.getByText("Recorded (1)")).toBeInTheDocument();
  });

  it("counts a text listed in both groups as blocking and lists it in both", () => {
    render(
      <DispatchProposalFields
        proposal={{ ...BASE_PROPOSAL, acceptance_clauses: ["shared"], recorded_clauses: ["shared"] }}
      />,
    );
    expect(screen.getByText("Blocking (1)")).toBeInTheDocument();
    expect(screen.getByTestId("dispatch-proposal-acceptance-clauses")).toHaveTextContent("shared");
    expect(screen.getByTestId("dispatch-proposal-recorded-clauses")).toHaveTextContent("shared");
  });

  it("renders no clause heading at all when both lists are empty", () => {
    render(
      <DispatchProposalFields
        proposal={{ ...BASE_PROPOSAL, acceptance_clauses: [], recorded_clauses: [] }}
      />,
    );
    expect(screen.queryByText(/^Blocking/)).toBeNull();
    expect(screen.queryByText(/^Recorded/)).toBeNull();
  });

  it("renders the counter-case when present", () => {
    render(<DispatchProposalFields proposal={BASE_PROPOSAL} />);
    expect(screen.getByTestId(COUNTER_CASE_ID)).toBeInTheDocument();
    expect(screen.getByTestId("dispatch-counter-case-against")).toHaveTextContent(
      COUNTER_CASE.against,
    );
    expect(screen.getByTestId("dispatch-counter-case-cheapest-80")).toHaveTextContent(
      COUNTER_CASE.cheapest_80,
    );
    expect(screen.getByTestId("dispatch-counter-case-cost-estimate")).toHaveTextContent(
      COUNTER_CASE.cost_estimate,
    );
  });

  it("gracefully skips the counter-case section for a legacy proposal without one", () => {
    const legacy = { ...BASE_PROPOSAL, counter_case: undefined } as unknown as DispatchProposal;
    render(<DispatchProposalFields proposal={legacy} />);
    expect(screen.queryByTestId(COUNTER_CASE_ID)).toBeNull();
  });
});

describe("counterCaseDescribedBy", () => {
  it("returns the counter-case section id when a counter case is present", () => {
    expect(counterCaseDescribedBy(COUNTER_CASE)).toBe(COUNTER_CASE_ID);
  });

  it("returns undefined when there is no counter case", () => {
    expect(counterCaseDescribedBy(undefined)).toBeUndefined();
  });
});
