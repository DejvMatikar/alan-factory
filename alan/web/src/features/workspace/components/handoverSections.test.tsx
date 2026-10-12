import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ScenarioOutcome, TestScenario } from "../../../api";
import {
  activateDisclosure,
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { TestScenariosSection, VerifierMetricsSection } from "./handoverSections";

describe("VerifierMetricsSection", () => {
  it("renders nothing when completeness, confidence, and gaps are all absent", () => {
    const { container } = render(
      <VerifierMetricsSection completeness={null} confidence={null} gaps={[]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when fields are undefined", () => {
    const { container } = render(
      <VerifierMetricsSection completeness={undefined} confidence={undefined} gaps={undefined} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the heading and completeness badge when only completeness is set", () => {
    render(<VerifierMetricsSection completeness={0.45} confidence={null} gaps={[]} />);
    expect(screen.getByRole("heading", { name: "Verifier Metrics" })).toBeInTheDocument();
    expect(screen.getByTestId("verifier-completeness")).toHaveTextContent("0.45");
    expect(screen.queryByTestId("verifier-confidence")).not.toBeInTheDocument();
  });

  it("renders both completeness and confidence badges when both are set", () => {
    render(<VerifierMetricsSection completeness={0.45} confidence={0.55} gaps={[]} />);
    expect(screen.getByTestId("verifier-completeness")).toHaveTextContent("0.45");
    expect(screen.getByTestId("verifier-confidence")).toHaveTextContent("0.55");
  });

  it("renders one list item per gap", () => {
    render(
      <VerifierMetricsSection
        completeness={0.45}
        confidence={0.55}
        gaps={["missed api surface", "missed perf numbers"]}
      />,
    );
    expect(screen.getByText("missed api surface")).toBeInTheDocument();
    expect(screen.getByText("missed perf numbers")).toBeInTheDocument();
  });

  it("renders gaps alone even when completeness and confidence are absent", () => {
    render(<VerifierMetricsSection completeness={null} confidence={null} gaps={["orphan gap"]} />);
    expect(screen.getByRole("heading", { name: "Verifier Metrics" })).toBeInTheDocument();
    expect(screen.getByText("orphan gap")).toBeInTheDocument();
  });

  it("applies the low-confidence badge class below 0.5", () => {
    render(<VerifierMetricsSection completeness={0.2} confidence={null} gaps={[]} />);
    expect(screen.getByTestId("verifier-completeness")).toHaveClass("text-text-muted");
  });

  it("applies the high-confidence badge class above 0.8", () => {
    render(<VerifierMetricsSection completeness={0.9} confidence={null} gaps={[]} />);
    expect(screen.getByTestId("verifier-completeness")).toHaveClass("text-green-400");
  });
});

const SCENARIO: TestScenario = {
  id: "s1",
  description: "does a thing",
  given: "precondition",
  when: "uv run pytest tests/test_x.py",
  then: "1 passed",
};

function outcome(status: ScenarioOutcome["status"], diagnostic = ""): ScenarioOutcome {
  return { scenario_id: "s1", status, diagnostic };
}

describe("TestScenariosSection", () => {
  it("renders nothing when scenarios is empty, regardless of results", () => {
    const { container } = render(
      <TestScenariosSection scenarios={[]} results={[outcome("PASS")]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the passed state with the ratified pill recipe", () => {
    render(<TestScenariosSection scenarios={[SCENARIO]} results={[outcome("PASS")]} />);
    const badge = screen.getByTestId("scenario-badge-passed-s1");
    expect(badge).toHaveTextContent("passed");
    expect(badge.className).toContain("inline-block rounded px-2 py-0.5 text-xs");
  });

  it("renders the failed state", () => {
    render(<TestScenariosSection scenarios={[SCENARIO]} results={[outcome("FAIL")]} />);
    const badge = screen.getByTestId("scenario-badge-failed-s1");
    expect(badge).toHaveTextContent("failed");
    expect(badge.className).toContain("inline-block rounded px-2 py-0.5 text-xs");
  });

  it("renders the skipped state", () => {
    render(<TestScenariosSection scenarios={[SCENARIO]} results={[outcome("SKIPPED")]} />);
    const badge = screen.getByTestId("scenario-badge-skipped-s1");
    expect(badge).toHaveTextContent("skipped");
    expect(badge.className).toContain("inline-block rounded px-2 py-0.5 text-xs");
  });

  it("renders the docs-only state", () => {
    render(<TestScenariosSection scenarios={[SCENARIO]} results={[outcome("DOCUMENTED")]} />);
    const badge = screen.getByTestId("scenario-badge-docs_only-s1");
    expect(badge).toHaveTextContent("docs only");
    expect(badge.className).toContain("inline-block rounded px-2 py-0.5 text-xs");
  });

  it("renders the advisory state, distinct from docs_only and passed", () => {
    render(
      <TestScenariosSection
        scenarios={[SCENARIO]}
        results={[outcome("ADVISORY", "then predicate '1 passed' not found in combined output")]}
      />,
    );
    const badge = screen.getByTestId("scenario-badge-advisory-s1");
    expect(badge).toHaveTextContent("advisory");
    expect(badge.className).toContain("bg-sky-500/15 text-sky-300");
    expect(screen.queryByTestId("scenario-badge-docs_only-s1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scenario-badge-passed-s1")).not.toBeInTheDocument();
    expect(screen.getByText("Why advisory")).toBeInTheDocument();
    expect(
      screen.getByText("then predicate '1 passed' not found in combined output"),
    ).toBeInTheDocument();
  });

  it("renders the timeout state, distinct from failed", () => {
    render(
      <TestScenariosSection
        scenarios={[SCENARIO]}
        results={[outcome("TIMEOUT", "timed out after 120s")]}
      />,
    );
    const badge = screen.getByTestId("scenario-badge-timeout-s1");
    expect(badge).toHaveTextContent("timeout");
    expect(badge.className).toContain("bg-red-500/15 text-red-300");
    expect(screen.queryByTestId("scenario-badge-failed-s1")).not.toBeInTheDocument();
    expect(screen.getByText("Why timeout")).toBeInTheDocument();
    expect(screen.getByText("timed out after 120s")).toBeInTheDocument();
  });

  it("renders the budget_exhausted state, distinct from skipped", () => {
    render(
      <TestScenariosSection
        scenarios={[SCENARIO]}
        results={[outcome("BUDGET_EXHAUSTED", "aggregate budget exhausted")]}
      />,
    );
    const badge = screen.getByTestId("scenario-badge-budget_exhausted-s1");
    expect(badge).toHaveTextContent("budget exhausted");
    expect(badge.className).toContain("bg-amber-500/15 text-amber-300");
    expect(screen.queryByTestId("scenario-badge-skipped-s1")).not.toBeInTheDocument();
    expect(screen.getByText("Why budget exhausted")).toBeInTheDocument();
  });

  it("renders the not-run state when no outcome matches the scenario id", () => {
    render(<TestScenariosSection scenarios={[SCENARIO]} results={[]} />);
    const badge = screen.getByTestId("scenario-badge-not_run-s1");
    expect(badge).toHaveTextContent("not run");
    expect(badge.className).toContain("inline-block rounded px-2 py-0.5 text-xs");
  });

  it("renders not-run even when the scenario has a test_ref but no matching outcome", () => {
    const scenarioWithRef: TestScenario = { ...SCENARIO, test_ref: "tests/test_x.py::test_it" };
    render(<TestScenariosSection scenarios={[scenarioWithRef]} results={[]} />);
    expect(screen.getByTestId("scenario-badge-not_run-s1")).toHaveTextContent("not run");
  });

  it("renders a disclosure with the diagnostic when non-empty", () => {
    render(
      <TestScenariosSection
        scenarios={[SCENARIO]}
        results={[outcome("SKIPPED", "pytest scenario not found (rc=4)")]}
      />,
    );
    expect(screen.getByText("Why skipped")).toBeInTheDocument();
    expect(screen.getByText("pytest scenario not found (rc=4)")).toBeInTheDocument();
  });

  it("starts the Why row closed with ▸ and hides the diagnostic until it is opened", async () => {
    const { container } = render(
      <TestScenariosSection
        scenarios={[SCENARIO]}
        results={[outcome("SKIPPED", "pytest scenario not found (rc=4)")]}
      />,
    );
    const details = container.querySelector<HTMLElement>("details")!;
    await expectDisclosureState(details, false);
    expect(disclosureSummary(details)).toHaveTextContent("Why skipped");
    expect(screen.getByText("pytest scenario not found (rc=4)")).not.toBeVisible();
  });

  it("opens and closes the Why row on click, Enter and Space", async () => {
    const { container } = render(
      <TestScenariosSection scenarios={[SCENARIO]} results={[outcome("FAIL", "exit code 1")]} />,
    );
    const details = container.querySelector<HTMLElement>("details")!;
    await expectTogglesOnEveryActivation(details);
    activateDisclosure(details, "Enter");
    await expectDisclosureState(details, true);
    expect(screen.getByText("exit code 1")).toBeVisible();
  });

  it("gives the Why row of an unmatched result the same closed-by-default disclosure", async () => {
    const driftedOutcome: ScenarioOutcome = {
      scenario_id: "new-id",
      status: "PASS",
      diagnostic: "renamed mid-flight",
    };
    const { container } = render(
      <TestScenariosSection scenarios={[SCENARIO]} results={[driftedOutcome]} />,
    );
    const details = container.querySelector<HTMLElement>("details")!;
    await expectDisclosureState(details, false);
    await expectTogglesOnEveryActivation(details);
  });

  it("renders a disclosure for a FAIL diagnostic too — not gated to skipped", () => {
    render(
      <TestScenariosSection
        scenarios={[SCENARIO]}
        results={[outcome("FAIL", "exit code 1")]}
      />,
    );
    expect(screen.getByText("Why failed")).toBeInTheDocument();
    expect(screen.getByText("exit code 1")).toBeInTheDocument();
  });

  it("renders no disclosure at all when diagnostic is empty", () => {
    const { container } = render(
      <TestScenariosSection scenarios={[SCENARIO]} results={[outcome("PASS", "")]} />,
    );
    expect(container.querySelector("details")).toBeNull();
  });

  it("renders no disclosure when the scenario is not run at all", () => {
    const { container } = render(
      <TestScenariosSection scenarios={[SCENARIO]} results={[]} />,
    );
    expect(container.querySelector("details")).toBeNull();
  });

  it("surfaces a scenario_result whose scenario_id has no matching test_scenarios entry under 'Other scenario results'", () => {
    const driftedOutcome: ScenarioOutcome = {
      scenario_id: "new-id",
      status: "PASS",
      diagnostic: "renamed mid-flight",
    };
    render(<TestScenariosSection scenarios={[SCENARIO]} results={[driftedOutcome]} />);
    expect(screen.getByTestId("scenario-badge-not_run-s1")).toHaveTextContent("not run");
    expect(screen.getByRole("heading", { name: "Other scenario results" })).toBeInTheDocument();
    const badge = screen.getByTestId("scenario-badge-passed-new-id");
    expect(badge).toHaveTextContent("passed");
    expect(screen.getByText("new-id")).toBeInTheDocument();
    expect(screen.getByText("Why passed")).toBeInTheDocument();
    expect(screen.getByText("renamed mid-flight")).toBeInTheDocument();
  });

  it("renders no disclosure for an unmatched result with an empty diagnostic", () => {
    const driftedOutcome: ScenarioOutcome = { scenario_id: "new-id", status: "SKIPPED", diagnostic: "" };
    render(<TestScenariosSection scenarios={[SCENARIO]} results={[driftedOutcome]} />);
    expect(screen.getByTestId("scenario-badge-skipped-new-id")).toBeInTheDocument();
    expect(screen.queryByText(/^Why /)).toBeNull();
  });

  it("omits 'Other scenario results' entirely when every result matches a scenario id", () => {
    render(<TestScenariosSection scenarios={[SCENARIO]} results={[outcome("PASS")]} />);
    expect(screen.queryByRole("heading", { name: "Other scenario results" })).not.toBeInTheDocument();
  });
});
