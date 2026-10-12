import { useCallback, useEffect } from "react";

import type { DispatchProposalResponse } from "../../../api";
import type { SummarizeFanout } from "./autoDispatch";
import { useAutoDispatchRun, type UseAutoDispatchRunResult } from "./useAutoDispatchRun";
import type { ChatComposerActions } from "./useChatComposerActions";
import type { CollisionInfo } from "./useCollisionCheck";
import type { PendingReadyProposal } from "./useDispatchProposalFlow";
import { useSessionSwitchReset } from "./useSessionSwitchReset";

export function makeOnSummarized(
  setTab: (t: "summary") => void,
  setSummaryMode: (m: "preview") => void,
  prefetchIssue: (content: string) => void,
  rerunCollision: (content: string) => Promise<CollisionInfo>,
  triggerPropose: (content: string) => Promise<DispatchProposalResponse | null>,
): (content: string) => SummarizeFanout {
  return (content: string) => {
    setTab("summary");
    setSummaryMode("preview");
    prefetchIssue(content);
    return { content, collision: rerunCollision(content), proposal: triggerPropose(content) };
  };
}

export interface AutoDispatchComposerConfig {
  sessionId: string | null;
  actions: ChatComposerActions;
  commit: (ready: PendingReadyProposal, instructions: string | null) => Promise<boolean>;
  setTab: (t: "chat" | "summary") => void;
  setSwitchedToSessionId: (id: string | null) => void;
  setSummaryMode: (m: "preview") => void;
  prefetchReset: () => void;
}

export interface AutoDispatchComposerResult {
  autoDispatch: UseAutoDispatchRunResult;
  composerActions: ChatComposerActions;
  handleAutoDispatch: () => void;
  handleLiveBriefChange: () => void;
}

export function useAutoDispatchComposer(config: AutoDispatchComposerConfig): AutoDispatchComposerResult {
  const { sessionId, actions, commit, setTab, setSwitchedToSessionId, setSummaryMode, prefetchReset } = config;
  const autoDispatch = useAutoDispatchRun();
  const { cancel: cancelAutoDispatch } = autoDispatch;

  const handleAutoDispatch = (): void => {
    autoDispatch.start({
      kind: "chat",
      summarize: actions.runSummarize,
      commit: (ready) => commit(ready, ready.proposal.instructions ?? null),
    });
  };

  const composerActions: ChatComposerActions = {
    ...actions,
    handleSend: (e) => {
      if (actions.draft.trim().length > 0) cancelAutoDispatch();
      actions.handleSend(e);
    },
    handleSummarize: () => {
      cancelAutoDispatch();
      actions.handleSummarize();
    },
    handleBriefEdit: (content) => {
      cancelAutoDispatch("brief_edited");
      actions.handleBriefEdit(content);
    },
  };

  const handleLiveBriefChange = (): void => {
    cancelAutoDispatch("brief_edited");
  };

  const handleSessionSwitch = useCallback(() => {
    setTab("chat");
    setSwitchedToSessionId(sessionId);
  }, [sessionId, setTab, setSwitchedToSessionId]);
  useSessionSwitchReset(sessionId, handleSessionSwitch);

  useEffect(() => {
    setSummaryMode("preview");
    return () => prefetchReset();
  }, [sessionId, prefetchReset, setSummaryMode]);

  useEffect(() => () => cancelAutoDispatch(), [sessionId, cancelAutoDispatch]);

  return { autoDispatch, composerActions, handleAutoDispatch, handleLiveBriefChange };
}
