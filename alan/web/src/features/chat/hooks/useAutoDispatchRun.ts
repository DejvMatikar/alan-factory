import { useCallback, useRef, useState } from "react";

import type { DispatchProposal, DispatchProposalResponse } from "../../../api";
import {
  advance,
  stage,
  AUTO_DISPATCH_IDLE,
  AUTO_DISPATCH_UNSET,
  type AutoDispatchState,
  type AutoDispatchStop,
  type CheckResult,
  type SummarizeFanout,
} from "./autoDispatch";
import { toInfo, type CollisionInfo } from "./useCollisionCheck";
import type { PendingReadyProposal } from "./useDispatchProposalFlow";

export interface ChatAutoDispatchPlan {
  kind: "chat";
  summarize: () => Promise<SummarizeFanout | null>;
  commit: (ready: PendingReadyProposal) => Promise<boolean>;
}

export interface AdvisorAutoDispatchPlan {
  kind: "advisor";
  propose: () => Promise<DispatchProposalResponse | null>;
  commit: (proposal: DispatchProposal) => Promise<boolean>;
}

export type AutoDispatchPlan = ChatAutoDispatchPlan | AdvisorAutoDispatchPlan;

export interface UseAutoDispatchRunResult {
  state: AutoDispatchState;
  start: (plan: AutoDispatchPlan) => void;
  stop: () => void;
  cancel: (reason?: AutoDispatchStop) => void;
}

type ReadyResponse = Extract<DispatchProposalResponse, { status: "ready" }>;

function gateCollision(info: CollisionInfo): CheckResult<CollisionInfo> {
  return info.status === "green" ? { value: info } : { stop: "overlap" };
}

function checkSummarize(fanout: SummarizeFanout | null): CheckResult<SummarizeFanout> {
  return fanout === null ? { stop: "summarize_failed" } : { value: fanout };
}

function checkProposal(res: DispatchProposalResponse | null): CheckResult<ReadyResponse> {
  if (res === null) return { stop: "propose_failed" };
  return res.status === "ready" ? { value: res } : { stop: "needs_info" };
}

function checkCommit(ok: boolean): CheckResult<true> {
  return ok ? { value: true } : { stop: "dispatch_failed" };
}

export function useAutoDispatchRun(): UseAutoDispatchRunResult {
  const generationRef = useRef(0);
  const [state, setState] = useState<AutoDispatchState>(AUTO_DISPATCH_IDLE);

  const cancel = useCallback((reason: AutoDispatchStop = "cancelled"): void => {
    generationRef.current += 1;
    setState((prev) => (prev.running ? { running: false, stage: null, stop: reason } : prev));
  }, []);

  const stop = useCallback((): void => cancel("cancelled"), [cancel]);

  const runChat = useCallback(async (plan: ChatAutoDispatchPlan, gen: number): Promise<void> => {
    const isStale = (): boolean => generationRef.current !== gen;
    const bail = (reason: AutoDispatchStop): void =>
      setState({ running: false, stage: null, stop: reason });

    setState({ running: true, stage: "summarizing", stop: null });
    const fanout = advance(await stage(plan.summarize(), checkSummarize, isStale), bail);
    if (fanout === AUTO_DISPATCH_UNSET) return;

    setState({ running: true, stage: "checking", stop: null });
    const collisionOutcome = advance(await stage(fanout.collision, gateCollision, isStale), bail);
    if (collisionOutcome === AUTO_DISPATCH_UNSET) return;

    setState({ running: true, stage: "proposing", stop: null });
    const proposed = advance(await stage(fanout.proposal, checkProposal, isStale), bail);
    if (proposed === AUTO_DISPATCH_UNSET) return;

    const ready: PendingReadyProposal = {
      proposal: proposed.proposal,
      briefContent: fanout.content,
      dispatchDescription: fanout.content,
      collision: proposed.collision,
    };

    setState({ running: true, stage: "dispatching", stop: null });
    const committed = advance(await stage(plan.commit(ready), checkCommit, isStale), bail);
    if (committed === AUTO_DISPATCH_UNSET) return;
    setState(AUTO_DISPATCH_IDLE);
  }, []);

  const runAdvisor = useCallback(async (plan: AdvisorAutoDispatchPlan, gen: number): Promise<void> => {
    const isStale = (): boolean => generationRef.current !== gen;
    const bail = (reason: AutoDispatchStop): void =>
      setState({ running: false, stage: null, stop: reason });

    setState({ running: true, stage: "proposing", stop: null });
    const proposed = advance(await stage(plan.propose(), checkProposal, isStale), bail);
    if (proposed === AUTO_DISPATCH_UNSET) return;
    const collision = toInfo(proposed.collision);

    setState({ running: true, stage: "checking", stop: null });
    const gated = advance(await stage(Promise.resolve(collision), gateCollision, isStale), bail);
    if (gated === AUTO_DISPATCH_UNSET) return;

    setState({ running: true, stage: "dispatching", stop: null });
    const committed = advance(await stage(plan.commit(proposed.proposal), checkCommit, isStale), bail);
    if (committed === AUTO_DISPATCH_UNSET) return;
    setState(AUTO_DISPATCH_IDLE);
  }, []);

  const start = useCallback(
    (plan: AutoDispatchPlan): void => {
      generationRef.current += 1;
      const gen = generationRef.current;
      if (plan.kind === "chat") void runChat(plan, gen);
      else void runAdvisor(plan, gen);
    },
    [runChat, runAdvisor],
  );

  return { state, start, stop, cancel };
}
