import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { OpenQuestion } from "../../../api";
import { OpenQuestionItem, OpenQuestionsSection } from "./OpenQuestionItem";

function question(overrides: Partial<OpenQuestion> = {}): OpenQuestion {
  return {
    id: "q1",
    question: "Which storage backend?",
    proposed_answer_space: ["duckdb", "postgres"],
    resolver_role: "human",
    blocking: true,
    prior_attempts: [],
    question_class: null,
    recommended: null,
    answer: null,
    option_resolvers: {},
    option_effects: {},
    ...overrides,
  };
}

function renderItem(q: OpenQuestion) {
  return render(
    <ul>
      <OpenQuestionItem question={q} />
    </ul>,
  );
}

describe("OpenQuestionItem", () => {
  it("renders the question text", () => {
    renderItem(question());
    expect(screen.getByText("Which storage backend?")).toBeInTheDocument();
  });

  it("renders a blocking tag when blocking is true", () => {
    renderItem(question({ blocking: true }));
    expect(screen.getByText("blocking")).toBeInTheDocument();
  });

  it("renders no blocking tag when blocking is false", () => {
    renderItem(question({ blocking: false }));
    expect(screen.queryByText("blocking")).not.toBeInTheDocument();
  });

  it("renders the resolver role", () => {
    renderItem(question({ resolver_role: "team_lead" }));
    expect(screen.getByText(/resolver: team_lead/)).toBeInTheDocument();
  });

  it("renders prior attempts when present", () => {
    renderItem(question({ prior_attempts: ["tried 80"] }));
    expect(screen.getByText(/tried 80/)).toBeInTheDocument();
  });

  it("renders no prior-attempts line when empty", () => {
    renderItem(question({ prior_attempts: [] }));
    expect(screen.queryByText(/Prior attempts/)).not.toBeInTheDocument();
  });

  it("renders each option as a discrete node with exact textContent, including commas and colons", () => {
    const options = ["duckdb", "postgres, sqlite: fallback (dev only)"];
    renderItem(question({ proposed_answer_space: options }));
    const items = screen.getAllByTestId("open-question-option");
    expect(items).toHaveLength(2);
    expect(items.map((el) => el.textContent)).toEqual(options);
  });

  it("renders no options list when proposed_answer_space is empty", () => {
    renderItem(question({ proposed_answer_space: [] }));
    expect(screen.queryByTestId("open-question-options")).not.toBeInTheDocument();
  });

  it("renders the recorded answer inside an <output> element", () => {
    renderItem(question({ answer: "8585" }));
    const el = screen.getByTestId("open-question-answer");
    expect(el.tagName).toBe("OUTPUT");
    expect(el).toHaveTextContent("Answer: 8585");
  });

  it("renders no answer line when unanswered", () => {
    renderItem(question({ answer: null }));
    expect(screen.queryByTestId("open-question-answer")).not.toBeInTheDocument();
  });

  it("renders no radio or button controls", () => {
    renderItem(question());
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders no Recommended or Dispatches markers", () => {
    renderItem(question());
    expect(screen.queryByText("Recommended")).not.toBeInTheDocument();
    expect(screen.queryByText(/Dispatches/)).not.toBeInTheDocument();
  });

  it("keeps the item reachable in the normal document flow, not hidden from assistive tech", () => {
    const { container } = renderItem(question());
    const li = container.querySelector("li");
    expect(li).not.toBeNull();
    expect(li).not.toHaveAttribute("aria-hidden");
    expect(li).not.toHaveAttribute("tabindex");
  });
});

describe("OpenQuestionsSection", () => {
  it("renders nothing when questions is empty", () => {
    const { container } = render(<OpenQuestionsSection questions={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a heading and one item per question", () => {
    const { container } = render(
      <OpenQuestionsSection questions={[question({ id: "q1" }), question({ id: "q2" })]} />,
    );
    expect(screen.getByRole("heading", { name: "Open Questions" })).toBeInTheDocument();
    const topLevelList = container.querySelector(":scope > div > ul");
    expect(topLevelList?.children).toHaveLength(2);
  });
});
