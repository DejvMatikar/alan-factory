import type { CollisionCheckResponse, DispatchProposal, DispatchProposalResponse } from "../../../api";

export interface PendingReadyProposal {
  proposal: DispatchProposal;
  briefContent: string;
  dispatchDescription: string;
  collision: CollisionCheckResponse;
}

type QuestionsSubmit = { kind: "idle" } | { kind: "submitting" } | { kind: "failed"; error: string };

export type ProposalFlowState =
  | { kind: "idle" }
  | { kind: "proposing" }
  | { kind: "propose_failed"; error: string }
  | { kind: "questions"; questions: string[]; answers: Record<number, string>; submit: QuestionsSubmit }
  | { kind: "ready"; ready: PendingReadyProposal }
  | { kind: "dispatch_failed"; error: string };

type ProposalFlowAction =
  | { type: "propose_started" }
  | {
      type: "propose_resolved";
      response: DispatchProposalResponse;
      briefContent: string;
      dispatchDescription?: string;
    }
  | { type: "propose_failed"; error: string }
  | { type: "answer_set"; index: number; value: string }
  | { type: "commit_started" }
  | { type: "commit_failed"; error: string };

export const PROPOSAL_FLOW_IDLE: ProposalFlowState = { kind: "idle" };

function handleProposeStarted(state: ProposalFlowState): ProposalFlowState {
  if (state.kind === "questions") return { ...state, submit: { kind: "submitting" } };
  return { kind: "proposing" };
}

function handleProposeResolved(
  _state: ProposalFlowState,
  action: Extract<ProposalFlowAction, { type: "propose_resolved" }>,
): ProposalFlowState {
  const { response, briefContent, dispatchDescription } = action;
  if (response.status === "ready") {
    return {
      kind: "ready",
      ready: {
        proposal: response.proposal,
        briefContent,
        dispatchDescription: dispatchDescription ?? briefContent,
        collision: response.collision,
      },
    };
  }
  if (response.questions.length === 0) return PROPOSAL_FLOW_IDLE;
  return { kind: "questions", questions: response.questions, answers: {}, submit: { kind: "idle" } };
}

function handleProposeFailed(
  state: ProposalFlowState,
  action: Extract<ProposalFlowAction, { type: "propose_failed" }>,
): ProposalFlowState {
  if (state.kind === "questions") return { ...state, submit: { kind: "failed", error: action.error } };
  return { kind: "propose_failed", error: action.error };
}

function handleAnswerSet(
  state: ProposalFlowState,
  action: Extract<ProposalFlowAction, { type: "answer_set" }>,
): ProposalFlowState {
  if (state.kind !== "questions") return state;
  return { ...state, answers: { ...state.answers, [action.index]: action.value } };
}

function handleCommitStarted(state: ProposalFlowState): ProposalFlowState {
  if (state.kind === "ready" || state.kind === "dispatch_failed") return PROPOSAL_FLOW_IDLE;
  return state;
}

function handleCommitFailed(
  state: ProposalFlowState,
  action: Extract<ProposalFlowAction, { type: "commit_failed" }>,
): ProposalFlowState {
  if (state.kind !== "idle") return state;
  return { kind: "dispatch_failed", error: action.error };
}

type HandlerMap = {
  [K in ProposalFlowAction["type"]]: (
    state: ProposalFlowState,
    action: Extract<ProposalFlowAction, { type: K }>,
  ) => ProposalFlowState;
};

const HANDLERS: HandlerMap = {
  propose_started: handleProposeStarted,
  propose_resolved: handleProposeResolved,
  propose_failed: handleProposeFailed,
  answer_set: handleAnswerSet,
  commit_started: handleCommitStarted,
  commit_failed: handleCommitFailed,
};

export function proposalFlowReducer(
  state: ProposalFlowState,
  action: ProposalFlowAction,
): ProposalFlowState {
  const handler = HANDLERS[action.type] as (
    s: ProposalFlowState,
    a: ProposalFlowAction,
  ) => ProposalFlowState;
  return handler(state, action);
}

export function flowErrorOf(state: ProposalFlowState): string | null {
  if (state.kind === "propose_failed" || state.kind === "dispatch_failed") return state.error;
  if (state.kind === "questions" && state.submit.kind === "failed") return state.submit.error;
  return null;
}

export function clarificationsOf(state: ProposalFlowState): { question: string; answer: string }[] {
  if (state.kind !== "questions") return [];
  return state.questions.map((q, i) => ({ question: q, answer: state.answers[i] ?? "" }));
}
