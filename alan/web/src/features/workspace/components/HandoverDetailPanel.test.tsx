import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Handover } from "../../../api";
import {
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { makeHandover } from "../../../test/workspaceFixtures";
import HandoverDetailPanel from "./HandoverDetailPanel";

const FULL_HANDOVER: Handover = makeHandover({
  handover_id: "h_001",
  task_id: "task_001",
  workspace_id: "task_001",
  from_team: "plan",
  to_team_suggestions: ["implementation"],
  iteration: 3,
  timestamp_utc: "2026-05-19T10:00:00Z",
  decisions: [
    { id: "d_high", statement: "Use TypeScript", rationale: "Type safety", confidence: 0.9, reversibility: "high" },
    { id: "d_mid", statement: "No caching", rationale: "Premature optimization", confidence: 0.5, reversibility: "medium" },
    { id: "d_low", statement: "Skip e2e", rationale: "Cost tradeoff", confidence: 0.2, reversibility: "low" },
  ],
  open_questions: [
    {
      id: "q1",
      question: "Which port?",
      proposed_answer_space: ["8080", "8585"],
      resolver_role: "team_lead",
      blocking: true,
      prior_attempts: ["tried 80"],
      question_class: null,
      recommended: null,
      answer: null,
      option_resolvers: {},
      option_effects: {},
    },
    {
      id: "q2",
      question: "Non-blocking question",
      proposed_answer_space: [],
      resolver_role: "any",
      blocking: false,
      prior_attempts: [],
      question_class: null,
      recommended: null,
      answer: null,
      option_resolvers: {},
      option_effects: {},
    },
  ],
  assumptions: ["Node 20+", "macOS only"],
  artifacts_produced: [
    { path: "/tmp/foo.ts", description: "Foo module" },
    { path: "abc1234567abcdef1234567abcdef1234567abcd", description: "Commit artifact" },
    { path: "https://github.com/DejvMatikar/alan/pull/99", description: "PR artifact" },
  ],
  full_synthesis: "Everything is set up.",
  forbidden_actions: ["Delete production DB"],
  prior_attempts_summary: "First attempt failed due to timeout.",
  pr_number: 42,
  test_scenarios: [
    {
      id: "ts1",
      description: "Header renders",
      given: "A handover is passed",
      when: "The component mounts",
      then: "Header is visible",
    },
  ],
});

function renderPanel(handover: Handover, repo: string | null = "DejvMatikar/alan") {
  return render(<HandoverDetailPanel handover={handover} repo={repo} />);
}

describe("HandoverDetailPanel", () => {
  it("renders all sections when fully populated", () => {
    renderPanel(FULL_HANDOVER);

    expect(screen.getByText("forward")).toBeInTheDocument();
    expect(screen.getByText("iter 3")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Decisions" })).toBeInTheDocument();
    expect(screen.getByText("Use TypeScript")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Open Questions" })).toBeInTheDocument();
    expect(screen.getByText("Which port?")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Assumptions" })).toBeInTheDocument();
    expect(screen.getByText("Node 20+")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Artifacts Produced" })).toBeInTheDocument();
    expect(screen.getByText("Foo module")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Forbidden Actions" })).toBeInTheDocument();
    expect(screen.getByText("Delete production DB")).toBeInTheDocument();

    expect(screen.getByText("Prior Attempts Summary")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Test Scenarios" })).toBeInTheDocument();
    expect(screen.getByText("Header renders")).toBeInTheDocument();

    expect(screen.getByText("Full Synthesis")).toBeInTheDocument();
    expect(screen.getByText("Everything is set up.")).toBeInTheDocument();
  });

  it("bounds its own overall height so an embedding run row cannot grow unbounded", () => {
    renderPanel(FULL_HANDOVER);
    const panel = screen.getByTestId("handover-detail-panel");
    expect(panel.className).toContain("max-h-");
    expect(panel.className).toContain("overflow-y-auto");
  });

  it("renders full synthesis without a fixed-height scroll cap", () => {
    const long = "a ".repeat(5000);
    renderPanel({ ...FULL_HANDOVER, full_synthesis: long });
    const region = screen.getByLabelText("Full synthesis");
    expect(region.className).not.toContain("max-h-");
    expect(region.className).not.toContain("overflow-y-auto");
  });

  it("renders full synthesis panel closed by default and opens on summary click", () => {
    renderPanel(FULL_HANDOVER);
    const content = screen.getByTestId("full-synthesis");
    expect(content).not.toBeVisible();

    fireEvent.click(screen.getByText("Full Synthesis"));

    expect(content).toBeVisible();
  });

  it("renders prior attempts summary panel closed by default and opens on summary click", () => {
    renderPanel(FULL_HANDOVER);
    const content = screen.getByTestId("prior-attempts-summary");
    expect(content).not.toBeVisible();

    fireEvent.click(screen.getByText("Prior Attempts Summary"));

    expect(content).toBeVisible();
  });

  it.each([
    { testId: "full-synthesis-disclosure", label: "Full Synthesis" },
    { testId: "prior-attempts-summary-disclosure", label: "Prior Attempts Summary" },
  ])("starts the $label section closed with ▸ and toggles on click, Enter and Space", async ({ testId, label }) => {
    renderPanel(FULL_HANDOVER);
    const details = screen.getByTestId(testId);
    await expectDisclosureState(details, false);
    expect(disclosureSummary(details)).toHaveTextContent(label);
    await expectTogglesOnEveryActivation(details);
  });

  it("hides decisions section when decisions is empty", () => {
    renderPanel({ ...FULL_HANDOVER, decisions: [] });
    expect(screen.queryByRole("heading", { name: "Decisions" })).not.toBeInTheDocument();
  });

  it("hides open questions section when open_questions is empty", () => {
    renderPanel({ ...FULL_HANDOVER, open_questions: [] });
    expect(screen.queryByRole("heading", { name: "Open Questions" })).not.toBeInTheDocument();
  });

  it("hides assumptions section when assumptions is empty", () => {
    renderPanel({ ...FULL_HANDOVER, assumptions: [] });
    expect(screen.queryByRole("heading", { name: "Assumptions" })).not.toBeInTheDocument();
  });

  it("hides artifacts section when artifacts_produced is empty", () => {
    renderPanel({ ...FULL_HANDOVER, artifacts_produced: [] });
    expect(screen.queryByRole("heading", { name: "Artifacts Produced" })).not.toBeInTheDocument();
  });

  it("hides forbidden actions section when forbidden_actions is empty", () => {
    renderPanel({ ...FULL_HANDOVER, forbidden_actions: [] });
    expect(screen.queryByRole("heading", { name: "Forbidden Actions" })).not.toBeInTheDocument();
  });

  it("hides prior attempts summary when prior_attempts_summary is null", () => {
    renderPanel({ ...FULL_HANDOVER, prior_attempts_summary: null });
    expect(screen.queryByText("Prior Attempts Summary")).not.toBeInTheDocument();
  });

  it("hides prior attempts summary when prior_attempts_summary is empty string", () => {
    renderPanel({ ...FULL_HANDOVER, prior_attempts_summary: "" });
    expect(screen.queryByText("Prior Attempts Summary")).not.toBeInTheDocument();
  });

  it("hides test scenarios section when test_scenarios is empty", () => {
    renderPanel({ ...FULL_HANDOVER, test_scenarios: [] });
    expect(screen.queryByRole("heading", { name: "Test Scenarios" })).not.toBeInTheDocument();
  });

  it("applies muted class to confidence badge for value 0.2", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByTestId("confidence-d_low")).toHaveClass("text-text-muted");
  });

  it("applies default class to confidence badge for value 0.5", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByTestId("confidence-d_mid")).toHaveClass("text-text-primary");
  });

  it("applies success class to confidence badge for value 0.9", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByTestId("confidence-d_high")).toHaveClass("text-green-400");
  });

  it("applies green class to high reversibility chip", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByTestId("reversibility-d_high")).toHaveClass("text-green-400");
  });

  it("applies yellow class to medium reversibility chip", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByTestId("reversibility-d_mid")).toHaveClass("text-yellow-400");
  });

  it("applies red class to low reversibility chip", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByTestId("reversibility-d_low")).toHaveClass("text-red-400");
  });

  it("renders GitHub PR link with correct URL when pr_number is set", () => {
    renderPanel(FULL_HANDOVER);
    const link = screen.getByTestId("pr-link");
    expect(link).toHaveAttribute("href", "https://github.com/DejvMatikar/alan/pull/42");
  });

  it("renders GitHub PR link scoped to a non-default repo", () => {
    renderPanel(FULL_HANDOVER, "acme-corp/sprocket");
    const link = screen.getByTestId("pr-link");
    expect(link).toHaveAttribute("href", "https://github.com/acme-corp/sprocket/pull/42");
  });

  it("renders a degraded, non-clickable PR reference when repo is unresolved", () => {
    renderPanel(FULL_HANDOVER, null);
    const el = screen.getByTestId("pr-link");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveTextContent("PR #42");
    expect(el).toHaveTextContent("repository unresolved");
  });

  it("does not render PR link when pr_number is null", () => {
    renderPanel({ ...FULL_HANDOVER, pr_number: null });
    expect(screen.queryByTestId("pr-link")).not.toBeInTheDocument();
  });

  it("renders all open questions including non-blocking ones", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByText("Which port?")).toBeInTheDocument();
    expect(screen.getByText("Non-blocking question")).toBeInTheDocument();
  });

  it("renders blocking marker only for blocking questions", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getAllByText("blocking")).toHaveLength(1);
  });

  it("renders prior attempts and each proposed answer as a discrete option row when non-empty", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByText(/tried 80/)).toBeInTheDocument();
    const options = screen.getAllByTestId("open-question-option");
    expect(options.map((el) => el.textContent)).toEqual(["8080", "8585"]);
  });

  it("renders test scenario with given/when/then lines", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByText("A handover is passed")).toBeInTheDocument();
    expect(screen.getByText("The component mounts")).toBeInTheDocument();
    expect(screen.getByText("Header is visible")).toBeInTheDocument();
  });

  it("renders the not-run badge when the handover carries no scenario_results (pre-1.19)", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.getByTestId("scenario-badge-not_run-ts1")).toBeInTheDocument();
    expect(screen.getByTestId("scenario-badge-not_run-ts1")).toHaveTextContent("not run");
  });

  it("renders the passed badge when scenario_results has a matching PASS outcome", () => {
    const handover: Handover = {
      ...FULL_HANDOVER,
      scenario_results: [{ scenario_id: "ts1", status: "PASS", diagnostic: "" }],
    };
    renderPanel(handover);
    expect(screen.getByTestId("scenario-badge-passed-ts1")).toBeInTheDocument();
    expect(screen.getByTestId("scenario-badge-passed-ts1")).toHaveTextContent("passed");
    expect(screen.queryByTestId("scenario-badge-not_run-ts1")).not.toBeInTheDocument();
  });

  it("renders a why-skipped disclosure when scenario_results carries a SKIPPED diagnostic", () => {
    const handover: Handover = {
      ...FULL_HANDOVER,
      scenario_results: [
        { scenario_id: "ts1", status: "SKIPPED", diagnostic: "pytest scenario not found (rc=4)" },
      ],
    };
    renderPanel(handover);
    expect(screen.getByTestId("scenario-badge-skipped-ts1")).toHaveTextContent("skipped");
    expect(screen.getByText("pytest scenario not found (rc=4)")).toBeInTheDocument();
  });

  it("renders local-path artifact with workspace artifacts API href", () => {
    renderPanel(FULL_HANDOVER);
    const link = screen.getByRole("link", { name: "Foo module" });
    expect(link).toHaveAttribute(
      "href",
      "/api/workspaces/task_001/artifacts?path=%2Ftmp%2Ffoo.ts",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).not.toHaveAttribute("rel");
  });

  it("renders SHA artifact with GitHub commit href", () => {
    renderPanel(FULL_HANDOVER);
    const link = screen.getByRole("link", { name: "Commit artifact" });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/DejvMatikar/alan/commit/abc1234567abcdef1234567abcdef1234567abcd",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders a degraded, non-clickable SHA artifact reference when repo is unresolved", () => {
    renderPanel(FULL_HANDOVER, null);
    expect(screen.queryByRole("link", { name: "Commit artifact" })).not.toBeInTheDocument();
    expect(screen.getByText(/Commit artifact/)).toHaveTextContent("repository unresolved");
  });

  it("renders http artifact with direct href", () => {
    renderPanel(FULL_HANDOVER);
    const link = screen.getByRole("link", { name: "PR artifact" });
    expect(link).toHaveAttribute("href", "https://github.com/DejvMatikar/alan/pull/99");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not render Verifier Metrics section when verifier fields are absent", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.queryByRole("heading", { name: "Verifier Metrics" })).not.toBeInTheDocument();
  });

  it("renders Verifier Metrics section before Open Questions when verifier fields are present", () => {
    renderPanel({
      ...FULL_HANDOVER,
      verifier_completeness: 0.45,
      verifier_confidence: 0.55,
      verifier_gaps: ["missed x"],
    });
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    const verifierIdx = headings.indexOf("Verifier Metrics");
    const openQuestionsIdx = headings.indexOf("Open Questions");
    expect(verifierIdx).toBeGreaterThanOrEqual(0);
    expect(openQuestionsIdx).toBeGreaterThanOrEqual(0);
    expect(verifierIdx).toBeLessThan(openQuestionsIdx);
    expect(screen.getByTestId("verifier-completeness")).toHaveTextContent("0.45");
    expect(screen.getByTestId("verifier-confidence")).toHaveTextContent("0.55");
    expect(screen.getByText("missed x")).toBeInTheDocument();
  });

  it("renders decisions as cards keyed by handover-decision-<id> with no table", () => {
    const { container } = renderPanel(FULL_HANDOVER);
    const card = screen.getByTestId("handover-decision-d_high");
    expect(card).toHaveTextContent("Use TypeScript");
    expect(card).toHaveTextContent("Type safety");
    expect(screen.getByTestId("handover-decision-d_mid")).toBeInTheDocument();
    expect(screen.getByTestId("handover-decision-d_low")).toBeInTheDocument();
    expect(container.querySelector("table")).toBeNull();
  });

  it("renders confidence and reversibility chips inside each decision card", () => {
    renderPanel(FULL_HANDOVER);
    const card = screen.getByTestId("handover-decision-d_mid");
    expect(card).toContainElement(screen.getByTestId("confidence-d_mid"));
    expect(card).toContainElement(screen.getByTestId("reversibility-d_mid"));
  });

  it("renders the recorded answer for an answered question inside an <output> element", () => {
    const handover: Handover = {
      ...FULL_HANDOVER,
      open_questions: [
        { ...FULL_HANDOVER.open_questions![0]!, answer: "8585" },
      ],
    };
    renderPanel(handover);
    const el = screen.getByTestId("open-question-answer");
    expect(el.tagName).toBe("OUTPUT");
    expect(el).toHaveTextContent("Answer: 8585");
  });

  it("renders no answer line for an unanswered question", () => {
    renderPanel(FULL_HANDOVER);
    expect(screen.queryByTestId("open-question-answer")).not.toBeInTheDocument();
  });

  it("keeps showing the recorded answer across a remount, simulating a page reload", () => {
    const handover: Handover = {
      ...FULL_HANDOVER,
      open_questions: [
        { ...FULL_HANDOVER.open_questions![0]!, answer: "8585" },
      ],
    };
    const { unmount } = renderPanel(handover);
    unmount();
    renderPanel(handover);
    expect(screen.getByTestId("open-question-answer")).toHaveTextContent("Answer: 8585");
  });

  it("renders mixed-case SHA artifact with GitHub commit href", () => {
    const handover = {
      ...FULL_HANDOVER,
      artifacts_produced: [{ path: "ABC1234567abcdef1234567abcdef1234567abcd", description: "Mixed SHA" }],
    };
    renderPanel(handover);
    const link = screen.getByRole("link", { name: "Mixed SHA" });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/DejvMatikar/alan/commit/ABC1234567abcdef1234567abcdef1234567abcd",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

});
