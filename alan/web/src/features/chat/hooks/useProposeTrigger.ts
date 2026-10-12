import type { DispatchProposalResponse } from "../../../api";
import type { UseDispatchProposalFlowResult } from "./useDispatchProposalFlow";
import type { UseProposePrefetchResult } from "./useProposePrefetch";

export interface ChatForComposerBase<B> {
  sendMessage: (content: string) => Promise<void>;
  summarize: () => Promise<B>;
  updateBrief: (content: string) => Promise<B>;
  refreshRunSummary: () => Promise<void>;
}

export interface UseProposeTriggerResult<T> {
  chatForComposer: T;
  triggerPropose: (content: string) => Promise<DispatchProposalResponse | null>;
}

// Both Build and Explore wire useProposePrefetch into their chat session the
// same way — cancel an outstanding prefetch before any other locking action,
// discard it on a brief edit, and issue the automatic post-summarize propose
// (which reuses prefetch.consume() so the Summary tab usually has a settled
// proposal by the time it's visible, see build_chat.md's "Frontend"
// section) — this hook is the one implementation both pages compose rather
// than reproducing it twice (they'd otherwise be flagged as jscpd
// duplication).
export function useProposeTrigger<B, T extends ChatForComposerBase<B>>(
  sessionId: string | null,
  chat: T,
  prefetch: UseProposePrefetchResult,
  flow: Pick<UseDispatchProposalFlowResult, "resolveImmediate" | "resolvePending">,
): UseProposeTriggerResult<T> {
  const triggerPropose = (content: string): Promise<DispatchProposalResponse | null> => {
    if (sessionId === null || content.trim().length === 0) return Promise.resolve(null);
    const outcome = prefetch.consume(sessionId, content);
    if ("immediate" in outcome) {
      return flow.resolveImmediate(outcome.immediate, content);
    }
    return flow.resolvePending(outcome.pending, content, () => {
      if (outcome.refreshOnSettle) void chat.refreshRunSummary();
    });
  };

  const chatForComposer: T = {
    ...chat,
    sendMessage: (content: string) => prefetch.cancelBeforeLockingAction(() => chat.sendMessage(content)),
    summarize: () => prefetch.cancelBeforeLockingAction(() => chat.summarize()),
    updateBrief: (content: string) => {
      prefetch.discardStale();
      return chat.updateBrief(content);
    },
  };

  return { chatForComposer, triggerPropose };
}
