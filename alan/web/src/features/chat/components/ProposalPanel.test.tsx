import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { CounterCase, DispatchProposal } from "../../../api";
import { settleDefaultEngineKey } from "../../../test/engineFixtures";
import type { PendingReadyProposal, ProposalFlowState } from "../hooks/proposalFlowReducer";
import ProposalPanel, { type ProposalPanelDoorConfig } from "./ProposalPanel";

const DOOR: ProposalPanelDoorConfig = {
  idleTestId: "test-idle",
  idleLabel: "Do the thing",
  pendingLabel: "Preparing…",
  questionsTestId: "test-questions",
  questionsActionLabel: "Answer and go",
  questionsBusyLabel: "Going…",
};

const COUNTER_CASE: CounterCase = {
  against: "scope is still shifting",
  cheapest_80: "ship the read path only",
  cost_estimate: "insufficient data",
};

const PROPOSAL: DispatchProposal = {
  team: "brainstorming",
  rationale: "ready to hand off",
  instructions: null,
  scope_audit: [],
  title: "the-title",
  acceptance_clauses: ["clause one"],
  counter_case: COUNTER_CASE,
};

const READY: PendingReadyProposal = {
  proposal: PROPOSAL,
  briefContent: "brief v1",
  dispatchDescription: "brief v1",
  collision: { state: "green", sentence: "clear", match: null },
};

const IDLE: ProposalFlowState = { kind: "idle" };
const PROPOSING: ProposalFlowState = { kind: "proposing" };
const QUESTIONS: ProposalFlowState = {
  kind: "questions",
  questions: ["Which db?"],
  answers: {},
  submit: { kind: "idle" },
};

function readyState(overrides: Partial<PendingReadyProposal> = {}): ProposalFlowState {
  return { kind: "ready", ready: { ...READY, ...overrides } };
}

const BASE_PROPS = {
  state: IDLE,
  liveBrief: "brief v1",
  dispatching: false,
  pageDispatching: false,
  pageDispatchError: null,
  door: DOOR,
  onAnswer: () => undefined,
  onSubmitAnswers: () => undefined,
  onDispatch: () => undefined,
  onConfirm: () => undefined,
};

describe("ProposalPanel", () => {
  it("idle: shows the door's idle button and no body content", () => {
    render(<ProposalPanel {...BASE_PROPS} />);
    const btn = screen.getByTestId(DOOR.idleTestId);
    expect(btn).toHaveTextContent(DOOR.idleLabel);
    expect(btn).not.toBeDisabled();
    expect(screen.queryByTestId("dispatch-proposal-rationale")).toBeNull();
  });

  it("pending: shows the pending line and a disabled idle-shaped button with the pending label", () => {
    render(<ProposalPanel {...BASE_PROPS} state={PROPOSING} />);
    expect(screen.getByTestId("proposal-panel-pending")).toHaveTextContent(DOOR.pendingLabel);
    const btn = screen.getByTestId(DOOR.idleTestId);
    expect(btn).toHaveTextContent(DOOR.pendingLabel);
    expect(btn).toBeDisabled();
  });

  it("error: shows an alert with the propose error, and an enabled retry button using the door's idle testid", () => {
    render(<ProposalPanel {...BASE_PROPS} state={{ kind: "propose_failed", error: "502: boom" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("502: boom");
    const btn = screen.getByTestId(DOOR.idleTestId);
    expect(btn).toHaveTextContent(DOOR.idleLabel);
    expect(btn).not.toBeDisabled();
  });

  it("error retry calls onDispatch", async () => {
    const onDispatch = vi.fn();
    render(
      <ProposalPanel
        {...BASE_PROPS}
        state={{ kind: "propose_failed", error: "boom" }}
        onDispatch={onDispatch}
      />,
    );
    await userEvent.click(screen.getByTestId(DOOR.idleTestId));
    expect(onDispatch).toHaveBeenCalledTimes(1);
  });

  it("dispatch_failed: renders exactly like idle plus a visible alert, since confirming clears the proposal before the panel falls back to any other state", () => {
    render(<ProposalPanel {...BASE_PROPS} state={{ kind: "dispatch_failed", error: "team not admitted" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("team not admitted");
    expect(screen.getByTestId(DOOR.idleTestId)).toHaveTextContent(DOOR.idleLabel);
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent("");
  });

  it("ready: a page-level dispatchError also renders alongside the settled proposal, combined at render", () => {
    render(<ProposalPanel {...BASE_PROPS} state={readyState()} pageDispatchError="boom" />);
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByTestId("dispatch-proposal-rationale")).toBeInTheDocument();
  });

  it("propose_failed: suppresses the page-level dispatch error banner since its own alert already covers the flow's error", () => {
    render(
      <ProposalPanel
        {...BASE_PROPS}
        state={{ kind: "propose_failed", error: "claude failed" }}
        pageDispatchError="workspace dispatch failed"
      />,
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("claude failed");
  });

  it("dispatch_failed: the flow's own error takes precedence over a page-level dispatch error", () => {
    render(
      <ProposalPanel
        {...BASE_PROPS}
        state={{ kind: "dispatch_failed", error: "flow dispatch broke" }}
        pageDispatchError="page dispatch broke"
      />,
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("flow dispatch broke");
  });

  it("questions: does not duplicate the page-level dispatchError banner outside QuestionsTab's own", () => {
    render(<ProposalPanel {...BASE_PROPS} state={QUESTIONS} pageDispatchError="team not admitted" />);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("idle: renders no alert when there is no page-level dispatchError", () => {
    render(<ProposalPanel {...BASE_PROPS} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("questions: renders the QuestionsTab body and no primary button", () => {
    render(<ProposalPanel {...BASE_PROPS} state={QUESTIONS} />);
    expect(screen.getByTestId("answer-0")).toBeInTheDocument();
    expect(screen.getByTestId(DOOR.questionsTestId)).toBeInTheDocument();
    expect(screen.queryByTestId(DOOR.idleTestId)).toBeNull();
    expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull();
  });

  it("questions: onAnswer and onSubmitAnswers wire through to the QuestionsTab", async () => {
    const onAnswer = vi.fn();
    const onSubmitAnswers = vi.fn();
    render(
      <ProposalPanel
        {...BASE_PROPS}
        state={{ kind: "questions", questions: ["Which db?"], answers: { 0: "Postgres" }, submit: { kind: "idle" } }}
        onAnswer={onAnswer}
        onSubmitAnswers={onSubmitAnswers}
      />,
    );
    await userEvent.type(screen.getByTestId("answer-0"), "!");
    expect(onAnswer).toHaveBeenCalled();
    await userEvent.click(screen.getByTestId(DOOR.questionsTestId));
    expect(onSubmitAnswers).toHaveBeenCalledTimes(1);
  });

  it("questions: shows busy and the submit's own error while a submit is in flight or has failed", () => {
    const { rerender } = render(
      <ProposalPanel
        {...BASE_PROPS}
        state={{ kind: "questions", questions: ["Which db?"], answers: {}, submit: { kind: "submitting" } }}
      />,
    );
    expect(screen.getByTestId(DOOR.questionsTestId)).toBeDisabled();

    rerender(
      <ProposalPanel
        {...BASE_PROPS}
        state={{
          kind: "questions",
          questions: ["Which db?"],
          answers: {},
          submit: { kind: "failed", error: "save failed" },
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("save failed");
  });

  it("ready: renders the shared proposal fields and a dispatch-proposal-confirm button described by the counter case", () => {
    render(<ProposalPanel {...BASE_PROPS} state={readyState()} />);
    expect(screen.getByTestId("dispatch-proposal-rationale")).toHaveTextContent("ready to hand off");
    expect(screen.getByTestId("dispatch-proposal-title")).toHaveTextContent("the-title");
    expect(screen.getByTestId("dispatch-proposal-acceptance-clauses")).toHaveTextContent("clause one");
    expect(screen.getByText("Blocking (1)")).toBeInTheDocument();
    const btn = screen.getByTestId("dispatch-proposal-confirm");
    expect(btn).toHaveTextContent("Dispatch");
    expect(btn).toHaveAttribute("aria-describedby", "dispatch-counter-case");
  });

  it("ready: renders a recorded group under its own heading and testid when the proposal carries recorded clauses", () => {
    const withRecorded = { ...PROPOSAL, recorded_clauses: ["the docs say X", "the docs say Y"] };
    render(<ProposalPanel {...BASE_PROPS} state={readyState({ proposal: withRecorded })} />);
    expect(screen.getByText("Blocking (1)")).toBeInTheDocument();
    expect(screen.getByText("Recorded (2)")).toBeInTheDocument();
    expect(screen.getByTestId("dispatch-proposal-recorded-clauses")).toHaveTextContent(
      "the docs say Y",
    );
  });

  it("ready: confirm click submits the (empty) instructions disclosure value as null, forwarding the state's own ready proposal", async () => {
    const onConfirm = vi.fn();
    render(<ProposalPanel {...BASE_PROPS} state={readyState()} onConfirm={onConfirm} />);
    await userEvent.click(screen.getByTestId("dispatch-proposal-confirm"));
    expect(onConfirm).toHaveBeenCalledWith(READY, null);
  });

  it("ready: typed instructions (behind the closed-by-default disclosure) are trimmed and forwarded on confirm", async () => {
    const onConfirm = vi.fn();
    render(<ProposalPanel {...BASE_PROPS} state={readyState()} onConfirm={onConfirm} />);
    const user = userEvent.setup();
    const details = screen.getByText("Instructions (optional)").closest("details");
    expect(details).not.toHaveAttribute("open");
    await user.click(screen.getByText("Instructions (optional)"));
    await user.type(screen.getByTestId("proposal-panel-instructions"), "  Focus on X.  ");
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    expect(onConfirm).toHaveBeenCalledWith(READY, "Focus on X.");
  });

  it("ready: disables and relabels the confirm button while dispatching", () => {
    render(<ProposalPanel {...BASE_PROPS} state={readyState()} dispatching />);
    const btn = screen.getByTestId("dispatch-proposal-confirm");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("Dispatching…");
  });

  it("idle: also reflects a busy dispatch (post-confirm, the ready proposal already cleared) via the door's idle testid, with no new status announcement", () => {
    render(<ProposalPanel {...BASE_PROPS} dispatching />);
    const btn = screen.getByTestId(DOOR.idleTestId);
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent(DOOR.pendingLabel);
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent("");
  });

  it("idle: also reflects a busy page-level dispatch via the door's idle testid", () => {
    render(<ProposalPanel {...BASE_PROPS} pageDispatching />);
    const btn = screen.getByTestId(DOOR.idleTestId);
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent(DOOR.pendingLabel);
  });

  it("stale: brief diverges from the settled proposal's briefContent — shows the repropose button and dims the container", () => {
    render(<ProposalPanel {...BASE_PROPS} state={readyState()} liveBrief="brief v2 (edited)" />);
    expect(screen.getByTestId("dispatch-proposal-rationale")).toBeInTheDocument();
    const btn = screen.getByTestId("dispatch-proposal-repropose");
    expect(btn).toHaveTextContent("Re-propose");
    expect(btn).not.toBeDisabled();
    expect(btn.className).toMatch(/opacity-70/);
    expect(screen.getByTestId("proposal-panel").className).toMatch(/border-border\/40/);
  });

  it("stale: repropose click calls onDispatch, and the button disables while re-proposing", () => {
    const onDispatch = vi.fn();
    const { rerender } = render(
      <ProposalPanel {...BASE_PROPS} state={readyState()} liveBrief="brief v2" onDispatch={onDispatch} />,
    );
    const btn = screen.getByTestId("dispatch-proposal-repropose");
    btn.click();
    expect(onDispatch).toHaveBeenCalledTimes(1);
    rerender(
      <ProposalPanel
        {...BASE_PROPS}
        state={PROPOSING}
        liveBrief="brief v2"
        onDispatch={onDispatch}
      />,
    );
    expect(screen.queryByTestId("dispatch-proposal-repropose")).toBeNull();
    expect(screen.getByTestId(DOOR.idleTestId)).toBeDisabled();
  });

  it("ready: not dimmed — only the stale state dims the container chrome", () => {
    render(<ProposalPanel {...BASE_PROPS} state={readyState()} />);
    expect(screen.getByTestId("proposal-panel").className).not.toMatch(/border-border\/40/);
  });

  it("renders a persistent status <output> whose DOM node identity survives a pending -> ready -> stale transition", () => {
    const { rerender } = render(<ProposalPanel {...BASE_PROPS} state={PROPOSING} />);
    const statusNode = screen.getByTestId("proposal-panel-status");
    expect(statusNode.tagName).toBe("OUTPUT");
    expect(statusNode).toHaveTextContent(DOOR.pendingLabel);

    rerender(<ProposalPanel {...BASE_PROPS} state={readyState()} />);
    expect(screen.getByTestId("proposal-panel-status")).toBe(statusNode);
    expect(statusNode).toHaveTextContent("Proposal ready");

    rerender(<ProposalPanel {...BASE_PROPS} state={readyState()} liveBrief="brief v2" />);
    expect(screen.getByTestId("proposal-panel-status")).toBe(statusNode);
    expect(statusNode).toHaveTextContent("Brief changed since this proposal");
  });

  it("questions: the status output announces open questions", () => {
    render(<ProposalPanel {...BASE_PROPS} state={QUESTIONS} />);
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
      "More information is needed",
    );
  });

  it("idle: the status output is empty", () => {
    render(<ProposalPanel {...BASE_PROPS} />);
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent("");
  });

  it("omits engine/roadmap-item provenance when neither is supplied", () => {
    render(<ProposalPanel {...BASE_PROPS} state={readyState()} />);
    expect(screen.queryByTestId("engine-badge-dispatch-proposal")).toBeNull();
    expect(screen.queryByTestId("dispatch-proposal-roadmap-item")).toBeNull();
  });

  it("forwards engineKey/roadmapItemLabel into the shared proposal fields", () => {
    settleDefaultEngineKey(null);
    render(
      <ProposalPanel
        {...BASE_PROPS}
        state={readyState()}
        engineKey="acme/widget"
        roadmapItemLabel="Ship the widget"
      />,
    );
    expect(screen.getByTestId("engine-badge-dispatch-proposal")).toHaveTextContent("acme/widget");
    expect(screen.getByTestId("dispatch-proposal-roadmap-item")).toHaveTextContent("Ship the widget");
  });
});
