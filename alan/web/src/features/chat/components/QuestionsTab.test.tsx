import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import QuestionsTab from "./QuestionsTab";

function renderTab(overrides: Partial<React.ComponentProps<typeof QuestionsTab>> = {}) {
  const onAnswer = vi.fn();
  const onDispatch = vi.fn();
  render(
    <QuestionsTab
      questions={["Which database?", "Which auth?"]}
      answers={{}}
      onAnswer={onAnswer}
      onDispatch={onDispatch}
      dispatching={false}
      error={null}
      actionLabel="Dispatch plan"
      busyLabel="Dispatching…"
      testId="dispatch-build"
      {...overrides}
    />,
  );
  return { onAnswer, onDispatch };
}

describe("QuestionsTab", () => {
  it("renders one textarea per question", () => {
    renderTab();
    expect(screen.getByText("Which database?")).toBeInTheDocument();
    expect(screen.getByText("Which auth?")).toBeInTheDocument();
    expect(screen.getByTestId("answer-0")).toBeInTheDocument();
    expect(screen.getByTestId("answer-1")).toBeInTheDocument();
  });

  it("disables the action button until every question is answered", async () => {
    renderTab();
    const user = userEvent.setup();
    expect(screen.getByTestId("dispatch-build")).toBeDisabled();
    await user.type(screen.getByTestId("answer-0"), "Postgres");
    expect(screen.getByTestId("dispatch-build")).toBeDisabled();
  });

  it("enables the action button once every question has a non-blank answer", () => {
    renderTab({ answers: { 0: "Postgres", 1: "OAuth" } });
    expect(screen.getByTestId("dispatch-build")).not.toBeDisabled();
  });

  it("treats a whitespace-only answer as unanswered", () => {
    renderTab({ answers: { 0: "Postgres", 1: "   " } });
    expect(screen.getByTestId("dispatch-build")).toBeDisabled();
  });

  it("calls onAnswer with the question index and new value", async () => {
    const { onAnswer } = renderTab();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("answer-0"), "P");
    expect(onAnswer).toHaveBeenCalledWith(0, "P");
  });

  it("calls onDispatch when the action button is clicked while enabled", async () => {
    const { onDispatch } = renderTab({ answers: { 0: "Postgres", 1: "OAuth" } });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("dispatch-build"));
    expect(onDispatch).toHaveBeenCalledTimes(1);
  });

  it("shows the busy label and disables the button while dispatching", () => {
    renderTab({ answers: { 0: "Postgres", 1: "OAuth" }, dispatching: true });
    const btn = screen.getByTestId("dispatch-build");
    expect(btn).toHaveTextContent("Dispatching…");
    expect(btn).toBeDisabled();
  });

  it("shows the actionLabel when idle", () => {
    renderTab({ answers: { 0: "Postgres", 1: "OAuth" } });
    expect(screen.getByTestId("dispatch-build")).toHaveTextContent("Dispatch plan");
  });

  it("renders an error banner when error is set", () => {
    renderTab({ error: "boom" });
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });

  it("renders no error banner when error is null", () => {
    renderTab({ error: null });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders the caller's own testId and labels for a different door", () => {
    renderTab({
      questions: ["Scope?"],
      answers: { 0: "yes" },
      actionLabel: "Dispatch consultant",
      busyLabel: "Preparing…",
      testId: "dispatch-explore-questions",
    });
    const btn = screen.getByTestId("dispatch-explore-questions");
    expect(btn).toHaveTextContent("Dispatch consultant");
  });
});
