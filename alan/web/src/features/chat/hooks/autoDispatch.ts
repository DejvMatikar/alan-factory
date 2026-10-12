import type { DispatchProposalResponse } from "../../../api";
import type { CollisionInfo } from "./useCollisionCheck";

export type AutoDispatchStage = "summarizing" | "checking" | "proposing" | "dispatching";

export type AutoDispatchStop =
  | "overlap"
  | "summarize_failed"
  | "propose_failed"
  | "needs_info"
  | "brief_edited"
  | "dispatch_failed"
  | "cancelled";

export interface AutoDispatchState {
  running: boolean;
  stage: AutoDispatchStage | null;
  stop: AutoDispatchStop | null;
}

export const AUTO_DISPATCH_IDLE: AutoDispatchState = { running: false, stage: null, stop: null };

export interface SummarizeFanout {
  content: string;
  collision: Promise<CollisionInfo>;
  proposal: Promise<DispatchProposalResponse | null>;
}

const STAGE_LABELS: Record<AutoDispatchStage, string> = {
  summarizing: "Summarizing…",
  checking: "Checking for overlapping work…",
  proposing: "Preparing the dispatch proposal…",
  dispatching: "Dispatching…",
};

export function stageLabel(stage: AutoDispatchStage): string {
  return STAGE_LABELS[stage];
}

const STOP_MESSAGES: Record<AutoDispatchStop, string> = {
  overlap:
    "Stopped before dispatching: the overlap check did not come back clear — review the " +
    "verdict below, then dispatch by hand if you still want to proceed.",
  summarize_failed: "Stopped before dispatching: the conversation could not be summarized.",
  propose_failed: "Stopped before dispatching: the dispatch proposal could not be prepared.",
  needs_info:
    "Stopped before dispatching: the proposal still has open questions — answer them below, " +
    "then dispatch by hand.",
  brief_edited: "Stopped before dispatching: the summary changed while auto dispatch was running.",
  dispatch_failed: "Stopped: the dispatch itself failed — see the error below.",
  cancelled: "Auto dispatch was cancelled.",
};

export function stopMessage(stop: AutoDispatchStop): string {
  return STOP_MESSAGES[stop];
}

export type CheckResult<U> = { value: U } | { stop: AutoDispatchStop };
export type StageOutcome<U> = CheckResult<U> | "stale";

export async function stage<T, U>(
  promise: Promise<T>,
  check: (value: T) => CheckResult<U>,
  isStale: () => boolean,
): Promise<StageOutcome<U>> {
  const value = await promise;
  if (isStale()) return "stale";
  return check(value);
}

export const AUTO_DISPATCH_UNSET: unique symbol = Symbol("auto-dispatch-unset");

export function advance<T>(
  outcome: StageOutcome<T>,
  onStop: (stop: AutoDispatchStop) => void,
): T | typeof AUTO_DISPATCH_UNSET {
  if (outcome === "stale") return AUTO_DISPATCH_UNSET;
  if ("stop" in outcome) {
    onStop(outcome.stop);
    return AUTO_DISPATCH_UNSET;
  }
  return outcome.value;
}
