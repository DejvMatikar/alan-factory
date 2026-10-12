import { describe, expect, it } from "vitest";

import type { CounterCase, DispatchProposal, DispatchProposalResponse } from "../../../api";
import {
  clarificationsOf,
  flowErrorOf,
  PROPOSAL_FLOW_IDLE,
  proposalFlowReducer,
  type ProposalFlowState,
} from "./proposalFlowReducer";

const COUNTER_CASE: CounterCase = {
  against: "the plan could slip",
  cheapest_80: "ship the thin slice",
  cost_estimate: "insufficient data",
};

const PROPOSAL: DispatchProposal = {
  team: "implementation_plan",
  rationale: "scoped and ready",
  instructions: null,
  scope_audit: [],
  title: "widget-title",
  acceptance_clauses: [],
  counter_case: COUNTER_CASE,
};

const COLLISION = { state: "green" as const, sentence: "clear", match: null };

function readyFixture(): DispatchProposalResponse {
  return { status: "ready", proposal: PROPOSAL, collision: COLLISION };
}

function questionsFixture(questions: string[]): DispatchProposalResponse {
  return { status: "need_more_info", questions };
}

const READY_STATE: ProposalFlowState = {
  kind: "ready",
  ready: { proposal: PROPOSAL, briefContent: "brief v1", dispatchDescription: "brief v1", collision: COLLISION },
};

const QUESTIONS_STATE: ProposalFlowState = {
  kind: "questions",
  questions: ["Which db?", "Which auth?"],
  answers: { 0: "Postgres" },
  submit: { kind: "idle" },
};

describe("proposalFlowReducer — initial state and first transitions", () => {
  it("starts idle", () => {
    expect(PROPOSAL_FLOW_IDLE).toEqual({ kind: "idle" });
  });

  it("idle + propose_started -> proposing", () => {
    expect(proposalFlowReducer(PROPOSAL_FLOW_IDLE, { type: "propose_started" })).toEqual({
      kind: "proposing",
    });
  });

  it("idle + propose_failed -> propose_failed", () => {
    expect(proposalFlowReducer(PROPOSAL_FLOW_IDLE, { type: "propose_failed", error: "boom" })).toEqual({
      kind: "propose_failed",
      error: "boom",
    });
  });

  it("idle + propose_resolved(need_more_info) -> questions", () => {
    expect(
      proposalFlowReducer(PROPOSAL_FLOW_IDLE, {
        type: "propose_resolved",
        response: questionsFixture(["Which env?"]),
        briefContent: "brief v1",
      }),
    ).toEqual({ kind: "questions", questions: ["Which env?"], answers: {}, submit: { kind: "idle" } });
  });

  it("idle + propose_resolved(ready) -> ready", () => {
    expect(
      proposalFlowReducer(PROPOSAL_FLOW_IDLE, {
        type: "propose_resolved",
        response: readyFixture(),
        briefContent: "brief v1",
      }),
    ).toEqual(READY_STATE);
  });

  it("idle + propose_resolved(need_more_info with no questions) -> idle, not a blank questions form", () => {
    expect(
      proposalFlowReducer(PROPOSAL_FLOW_IDLE, {
        type: "propose_resolved",
        response: questionsFixture([]),
        briefContent: "brief v1",
      }),
    ).toEqual(PROPOSAL_FLOW_IDLE);
  });

  it("ready + propose_resolved(need_more_info with no questions) -> idle, replacing the stale proposal", () => {
    expect(
      proposalFlowReducer(READY_STATE, {
        type: "propose_resolved",
        response: questionsFixture([]),
        briefContent: "brief v2",
      }),
    ).toEqual(PROPOSAL_FLOW_IDLE);
  });
});

describe("proposalFlowReducer — precedence between concurrent propose transitions", () => {
  it("questions over proposing: propose_started while questions are open only marks the submit as submitting", () => {
    expect(proposalFlowReducer(QUESTIONS_STATE, { type: "propose_started" })).toEqual({
      ...QUESTIONS_STATE,
      submit: { kind: "submitting" },
    });
  });

  it("questions over error: propose_failed while questions are open marks the submit as failed and keeps the answers", () => {
    expect(
      proposalFlowReducer(QUESTIONS_STATE, { type: "propose_failed", error: "re-propose failed" }),
    ).toEqual({ ...QUESTIONS_STATE, submit: { kind: "failed", error: "re-propose failed" } });
  });

  it("proposing over a leftover error: propose_started from propose_failed goes to proposing", () => {
    const state: ProposalFlowState = { kind: "propose_failed", error: "stale error" };
    expect(proposalFlowReducer(state, { type: "propose_started" })).toEqual({ kind: "proposing" });
  });

  it("proposing over a stale proposal: propose_started from ready goes to proposing", () => {
    expect(proposalFlowReducer(READY_STATE, { type: "propose_started" })).toEqual({ kind: "proposing" });
  });

  it("error over a settled proposal: propose_failed from ready replaces it with propose_failed", () => {
    expect(proposalFlowReducer(READY_STATE, { type: "propose_failed", error: "boom" })).toEqual({
      kind: "propose_failed",
      error: "boom",
    });
  });
});

describe("proposalFlowReducer — last-result-wins", () => {
  const startingStates: [string, ProposalFlowState][] = [
    ["idle", PROPOSAL_FLOW_IDLE],
    ["proposing", { kind: "proposing" }],
    ["propose_failed", { kind: "propose_failed", error: "old" }],
    ["ready", READY_STATE],
    ["dispatch_failed", { kind: "dispatch_failed", error: "old" }],
    ["questions", QUESTIONS_STATE],
  ];

  it.each(startingStates)("propose_resolved(ready) from %s always lands on ready", (_label, state) => {
    const result = proposalFlowReducer(state, {
      type: "propose_resolved",
      response: readyFixture(),
      briefContent: "brief v2",
    });
    expect(result).toEqual({
      kind: "ready",
      ready: { proposal: PROPOSAL, briefContent: "brief v2", dispatchDescription: "brief v2", collision: COLLISION },
    });
  });

  it("propose_resolved carries a distinct dispatchDescription when supplied", () => {
    const result = proposalFlowReducer(QUESTIONS_STATE, {
      type: "propose_resolved",
      response: readyFixture(),
      briefContent: "original brief",
      dispatchDescription: "original brief\n\n## Clarifications\n\n### Which db?\nPostgres",
    });
    expect(result).toEqual({
      kind: "ready",
      ready: {
        proposal: PROPOSAL,
        briefContent: "original brief",
        dispatchDescription: "original brief\n\n## Clarifications\n\n### Which db?\nPostgres",
        collision: COLLISION,
      },
    });
  });
});

describe("proposalFlowReducer — commit lifecycle", () => {
  it("commit_started resets a ready proposal to idle", () => {
    expect(proposalFlowReducer(READY_STATE, { type: "commit_started" })).toEqual(PROPOSAL_FLOW_IDLE);
  });

  it("commit_started resets a dispatch_failed state to idle", () => {
    const state: ProposalFlowState = { kind: "dispatch_failed", error: "boom" };
    expect(proposalFlowReducer(state, { type: "commit_started" })).toEqual(PROPOSAL_FLOW_IDLE);
  });

  it("commit_started leaves every other kind unchanged", () => {
    const state: ProposalFlowState = { kind: "proposing" };
    expect(proposalFlowReducer(state, { type: "commit_started" })).toEqual(state);
  });

  it("commit_failed overwrites idle (the commit's own commit_started reset) with dispatch_failed", () => {
    expect(
      proposalFlowReducer(PROPOSAL_FLOW_IDLE, { type: "commit_failed", error: "dispatch broke" }),
    ).toEqual({ kind: "dispatch_failed", error: "dispatch broke" });
  });

  it("commit_failed is ignored once a newer proposal replaced the reset idle state, so a late failure cannot discard it", () => {
    expect(proposalFlowReducer(READY_STATE, { type: "commit_failed", error: "dispatch broke" })).toEqual(
      READY_STATE,
    );
  });

  it("commit_failed is ignored once open questions replaced the reset idle state, so a late failure cannot discard them", () => {
    expect(
      proposalFlowReducer(QUESTIONS_STATE, { type: "commit_failed", error: "dispatch broke" }),
    ).toEqual(QUESTIONS_STATE);
  });

  it("an old commit's failure cannot discard a newer proposal accepted since its own commit_started reset", () => {
    const afterCommitStarted = proposalFlowReducer(READY_STATE, { type: "commit_started" });
    const newerReady = proposalFlowReducer(afterCommitStarted, {
      type: "propose_resolved",
      response: readyFixture(),
      briefContent: "brief v2",
    });
    expect(
      proposalFlowReducer(newerReady, { type: "commit_failed", error: "old commit's dispatch broke" }),
    ).toEqual(newerReady);
  });
});

describe("proposalFlowReducer — answer_set", () => {
  it("records a typed answer while questions are open", () => {
    expect(proposalFlowReducer(QUESTIONS_STATE, { type: "answer_set", index: 1, value: "SSO" })).toEqual({
      ...QUESTIONS_STATE,
      answers: { 0: "Postgres", 1: "SSO" },
    });
  });

  it("is a no-op outside the questions kind", () => {
    expect(proposalFlowReducer(PROPOSAL_FLOW_IDLE, { type: "answer_set", index: 0, value: "x" })).toEqual(
      PROPOSAL_FLOW_IDLE,
    );
  });
});

describe("flowErrorOf", () => {
  it("is null for idle", () => {
    expect(flowErrorOf(PROPOSAL_FLOW_IDLE)).toBeNull();
  });

  it("is null for proposing", () => {
    expect(flowErrorOf({ kind: "proposing" })).toBeNull();
  });

  it("is the error for propose_failed", () => {
    expect(flowErrorOf({ kind: "propose_failed", error: "boom" })).toBe("boom");
  });

  it("is null for ready", () => {
    expect(flowErrorOf(READY_STATE)).toBeNull();
  });

  it("is the error for dispatch_failed", () => {
    expect(flowErrorOf({ kind: "dispatch_failed", error: "dispatch broke" })).toBe("dispatch broke");
  });

  it("is null for questions with an idle submit", () => {
    expect(flowErrorOf(QUESTIONS_STATE)).toBeNull();
  });

  it("is null for questions with a submitting submit", () => {
    expect(flowErrorOf({ ...QUESTIONS_STATE, submit: { kind: "submitting" } })).toBeNull();
  });

  it("is the submit error for questions with a failed submit", () => {
    expect(flowErrorOf({ ...QUESTIONS_STATE, submit: { kind: "failed", error: "save failed" } })).toBe(
      "save failed",
    );
  });
});

describe("clarificationsOf", () => {
  it("pairs each question with its typed answer, defaulting a missing one to an empty string", () => {
    expect(clarificationsOf(QUESTIONS_STATE)).toEqual([
      { question: "Which db?", answer: "Postgres" },
      { question: "Which auth?", answer: "" },
    ]);
  });

  it("is empty for a non-questions kind", () => {
    expect(clarificationsOf(READY_STATE)).toEqual([]);
  });
});
