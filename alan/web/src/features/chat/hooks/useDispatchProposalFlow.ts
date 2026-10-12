import { createElement, useReducer, type ReactElement } from "react";

import { composeClarifications, type CounterCase, type DispatchProposalResponse } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import ProposalPanel, { type ProposalPanelDoorConfig } from "../components/ProposalPanel";
import {
  clarificationsOf,
  proposalFlowReducer,
  PROPOSAL_FLOW_IDLE,
  type PendingReadyProposal,
  type ProposalFlowState,
} from "./proposalFlowReducer";
import { useAction } from "../../../ui/useAction";

export type { PendingReadyProposal };

export interface DispatchWorkspaceFnOptions {
  acceptanceClauses?: string[] | undefined;
  recordedClauses?: string[] | undefined;
  counterCase?: CounterCase | undefined;
  instructions?: string | null | undefined;
  engineKey?: string | null | undefined;
}

export type DispatchWorkspaceFn = (
  title: string,
  description: string,
  options?: DispatchWorkspaceFnOptions,
) => Promise<boolean>;

export interface UseDispatchProposalFlowOptions {
  sessionId: string | null;
  engineKey?: string | null | undefined;
  roadmapItemLabel?: string | null | undefined;
  propose: (sessionId: string) => Promise<DispatchProposalResponse>;
  putBrief: (sessionId: string, content: string) => Promise<{ content: string }>;
  dispatchWorkspace: DispatchWorkspaceFn;
  refreshRunSummary: () => Promise<void>;
  workspaceDispatching: boolean;
  workspaceDispatchError: string | null;
  door: ProposalPanelDoorConfig;
}

export interface UseDispatchProposalFlowResult {
  state: ProposalFlowState;
  setAnswer: (i: number, value: string) => void;
  resolveImmediate: (
    res: DispatchProposalResponse,
    briefContent: string,
  ) => Promise<DispatchProposalResponse | null>;
  resolvePending: (
    pending: Promise<DispatchProposalResponse>,
    briefContent: string,
    onSettleExtra?: () => void,
  ) => Promise<DispatchProposalResponse | null>;
  requestPropose: (briefContent: string) => void;
  submitAnswers: (briefContent: string) => void;
  commit: (ready: PendingReadyProposal, instructions: string | null) => Promise<boolean>;
  renderProposal: (liveBrief: string) => ReactElement;
}

export function useDispatchProposalFlow(
  opts: UseDispatchProposalFlowOptions,
): UseDispatchProposalFlowResult {
  const {
    sessionId,
    engineKey,
    roadmapItemLabel,
    propose,
    putBrief,
    dispatchWorkspace,
    refreshRunSummary,
    workspaceDispatching,
    workspaceDispatchError,
    door,
  } = opts;
  const [state, dispatch] = useReducer(proposalFlowReducer, PROPOSAL_FLOW_IDLE);
  const { busy, run } = useAction();

  const resolveImmediate = (
    res: DispatchProposalResponse,
    briefContent: string,
  ): Promise<DispatchProposalResponse | null> => {
    dispatch({ type: "propose_resolved", response: res, briefContent });
    return Promise.resolve(res);
  };

  const resolvePending = (
    pending: Promise<DispatchProposalResponse>,
    briefContent: string,
    onSettleExtra?: () => void,
  ): Promise<DispatchProposalResponse | null> => {
    dispatch({ type: "propose_started" });
    return pending
      .then((res) => {
        dispatch({ type: "propose_resolved", response: res, briefContent });
        onSettleExtra?.();
        return res;
      })
      .catch((err: unknown) => {
        dispatch({ type: "propose_failed", error: errorMessageOf(err) });
        return null;
      });
  };

  const requestPropose = (briefContent: string): void => {
    /* v8 ignore next */
    if (sessionId === null) return;
    void resolvePending(propose(sessionId), briefContent);
  };

  const setAnswer = (i: number, value: string): void => {
    dispatch({ type: "answer_set", index: i, value });
  };

  const submitAnswers = (briefContent: string): void => {
    /* v8 ignore next */
    if (sessionId === null) return;
    const sid = sessionId;
    const description = composeClarifications(briefContent, clarificationsOf(state));
    dispatch({ type: "propose_started" });
    void putBrief(sid, description)
      .then(() => propose(sid))
      .then((res) => {
        dispatch({
          type: "propose_resolved",
          response: res,
          briefContent,
          dispatchDescription: description,
        });
        void refreshRunSummary();
      })
      .catch((err: unknown) => dispatch({ type: "propose_failed", error: errorMessageOf(err) }));
  };

  const commit = (ready: PendingReadyProposal, instructions: string | null): Promise<boolean> => {
    const { proposal, dispatchDescription } = ready;
    dispatch({ type: "commit_started" });
    return run(() =>
      dispatchWorkspace(proposal.title ?? "", dispatchDescription, {
        acceptanceClauses: proposal.acceptance_clauses ?? [],
        recordedClauses: proposal.recorded_clauses ?? [],
        counterCase: proposal.counter_case,
        instructions,
        engineKey,
      }),
    ).catch((err: unknown) => {
      dispatch({ type: "commit_failed", error: errorMessageOf(err) });
      return false;
    });
  };

  const renderProposal = (liveBrief: string): ReactElement =>
    createElement(ProposalPanel, {
      state,
      liveBrief,
      engineKey,
      roadmapItemLabel,
      dispatching: busy,
      pageDispatching: workspaceDispatching,
      pageDispatchError: workspaceDispatchError,
      door,
      onAnswer: setAnswer,
      onSubmitAnswers: () => submitAnswers(liveBrief),
      onDispatch: () => {
        if (liveBrief.trim().length === 0) return;
        requestPropose(liveBrief);
      },
      onConfirm: (ready, instructions) => commit(ready, instructions).then(() => undefined),
    });

  return {
    state,
    setAnswer,
    resolveImmediate,
    resolvePending,
    requestPropose,
    submitAnswers,
    commit,
    renderProposal,
  };
}
