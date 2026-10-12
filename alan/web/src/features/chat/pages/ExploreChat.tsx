import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api, type ConsultantInfo } from "../../../api";
import { resolveExploreEngineKey } from "../hooks/chatEngineBinding";
import { archivedTaskIdOf, isTurnRunning } from "../hooks/chatSessionReducer";
import AutoDispatchStatusRow from "../components/AutoDispatchStatusRow";
import ChatPanel from "../components/ChatPanel";
import ChatScaffold from "../components/ChatScaffold";
import ChatSummaryTab, { type SummaryMode } from "../components/ChatSummaryTab";
import { RepoChooserGate } from "../components/RepoChooserModal";
import { useChatEngineSelfCorrect } from "../../../shell/LegacyChatRedirect";
import ErrorBanner from "../../../ui/ErrorBanner";
import { useAutoDispatchComposer, makeOnSummarized } from "../hooks/useAutoDispatchComposer";
import { useChatComposerActions } from "../hooks/useChatComposerActions";
import { useChatDelete } from "../../../hooks/useChatDelete";
import { useCollisionCheck } from "../hooks/useCollisionCheck";
import { useConsultants } from "../../../hooks/useConsultants";
import { useDispatchProposalFlow } from "../hooks/useDispatchProposalFlow";
import { useDispatchWorkspace } from "../hooks/useDispatchWorkspace";
import { useExploreChatSession } from "../hooks/useExploreChatSession";
import { useProposePrefetch } from "../hooks/useProposePrefetch";
import { useProposeTrigger } from "../hooks/useProposeTrigger";
import { useRepoSelection } from "../hooks/useRepoSelection";
import { useRetryProvisioning } from "../hooks/useRetryProvisioning";
import { useStickToBottom } from "../hooks/useStickToBottom";
import { useUrlDraftSession } from "../hooks/useUrlDraftSession";

const EXPLORE_DOOR = {
  idleTestId: "dispatch-explore",
  idleLabel: "Dispatch consultant",
  pendingLabel: "Preparing…",
  questionsTestId: "dispatch-explore-questions",
  questionsActionLabel: "Dispatch consultant",
  questionsBusyLabel: "Dispatching…",
};

function getBriefKey(brief: { updated_at: string } | null | undefined): string {
  return brief == null ? "no-brief" : brief.updated_at;
}

type Tab = "chat" | "summary";

interface ExploreChatSessionProps {
  consultantKey: string;
  consultantLabel: string;
  sessionId: string | null;
  onCleared: (newId: string) => void;
}

function ExploreChatSession({
  consultantKey,
  consultantLabel,
  sessionId,
  onCleared,
}: Readonly<ExploreChatSessionProps>) {
  const [tab, setTab] = useState<Tab>("chat");
  const [summaryMode, setSummaryMode] = useState<SummaryMode>("preview");
  const [resumedOnMount] = useState(() => sessionId !== null);
  const [switchedToSessionId, setSwitchedToSessionId] = useState<string | null>(null);
  const chat = useExploreChatSession(sessionId, consultantKey);
  const navigate = useNavigate();
  useChatEngineSelfCorrect("explore", chat.engineKey);
  const archivedTaskId = archivedTaskIdOf(chat.session);
  useEffect(() => {
    if (archivedTaskId !== null) {
      void navigate(`/workspaces/${archivedTaskId}/prep-chat`, { replace: true });
    }
  }, [archivedTaskId, navigate]);
  const prefetch = useProposePrefetch(sessionId, chat.refreshRunSummary, {
    propose: api.proposeExploreDispatch,
    interrupt: api.interruptExploreChatSession,
  });
  const { reset: prefetchReset } = prefetch;
  const { anchorRef: scrollAnchorRef, pinned, jumpToLatest } = useStickToBottom([
    chat.messages,
    chat.toolActivity,
    isTurnRunning(chat.turn),
  ]);
  const deleteFlow = useChatDelete(
    sessionId,
    api.interruptExploreChatSession,
    api.deleteExploreChatSession,
  );
  const { collisionInfo, rerun: rerunCollision } = useCollisionCheck("", {
    auto: false,
    chatFamily: "explore",
    sessionId: sessionId ?? undefined,
    onSettled: () => {
      void chat.refreshRunSummary();
    },
  });

  const { dispatchWorkspace, dispatching, dispatchError } = useDispatchWorkspace(
    consultantKey,
    "explore",
  );

  const flow = useDispatchProposalFlow({
    sessionId,
    engineKey: chat.engineKey,
    propose: api.proposeExploreDispatch,
    putBrief: api.putExploreChatBrief,
    dispatchWorkspace: (title, description, options) =>
      dispatchWorkspace(title, description, {
        ...options,
        projectId: chat.projectId,
        prepChatSessionId: sessionId,
      }),
    refreshRunSummary: chat.refreshRunSummary,
    workspaceDispatching: dispatching,
    workspaceDispatchError: dispatchError,
    door: EXPLORE_DOOR,
  });

  // The one place a fresh propose is triggered without an explicit click —
  // right after summarize settles, mirroring the eager prefetch.issue() call
  // right above it, so the Summary tab usually already has a settled
  // proposal by the time the person looks at it (prefetch parity with Build).
  const { chatForComposer, triggerPropose } = useProposeTrigger(sessionId, chat, prefetch, flow);
  const actions = useChatComposerActions(
    chatForComposer,
    onCleared,
    makeOnSummarized(setTab, setSummaryMode, prefetch.issue, rerunCollision, triggerPropose),
  );
  const emptyHint = `Chat with the ${consultantLabel} advisor. Summarize when ready, then dispatch.`;

  const { autoDispatch, composerActions, handleAutoDispatch, handleLiveBriefChange } =
    useAutoDispatchComposer({
      sessionId,
      actions,
      commit: flow.commit,
      setTab,
      setSwitchedToSessionId,
      setSummaryMode,
      prefetchReset,
    });

  const handleRetryProvisioning = useRetryProvisioning(chat);

  const sessionReady = sessionId !== null;
  const briefKey = getBriefKey(chat.brief);

  return (
    <>
      {deleteFlow.modal}
      <ChatScaffold
        title={`Explore / ${consultantLabel}`}
        tab={tab}
        onTab={setTab}
        runSummary={chat.runSummary}
        collisionRunSummary={chat.collisionRunSummary}
        focusHeadingKey={resumedOnMount ? sessionId : switchedToSessionId}
        autoDispatchStatus={
          <AutoDispatchStatusRow state={autoDispatch.state} onStop={autoDispatch.stop} />
        }
      >
        {tab === "chat" && (
          <ChatPanel
            scrollAnchorRef={scrollAnchorRef}
            chat={chat}
            actions={composerActions}
            emptyHint={emptyHint}
            sessionReady={sessionReady}
            rows={12}
            onRetryProvisioning={handleRetryProvisioning}
            deleteAction={{
              onRequestDelete: deleteFlow.requestDelete,
              deleting: deleteFlow.deleting,
              deleteError: deleteFlow.deleteError,
            }}
            onAutoDispatch={handleAutoDispatch}
            autoDispatch={autoDispatch.state}
            pinned={pinned}
            onJumpToLatest={jumpToLatest}
          />
        )}
        {tab === "summary" && (
          <ChatSummaryTab
            key={briefKey}
            brief={chat.brief}
            onEdit={composerActions.handleBriefEdit}
            renderProposal={flow.renderProposal}
            collisionInfo={collisionInfo}
            onRerunCollision={(brief) => void rerunCollision(brief)}
            mode={summaryMode}
            onModeChange={setSummaryMode}
            onLiveBriefChange={handleLiveBriefChange}
          />
        )}
      </ChatScaffold>
    </>
  );
}

function exploreChatEnabled(
  consultantKey: string,
  reposLoaded: boolean,
  resolvedEngineKey: string | null,
): boolean {
  return consultantKey !== "" && reposLoaded && resolvedEngineKey !== null;
}

interface ResolvedConsultant {
  entry: ConsultantInfo | undefined;
  key: string;
  label: string;
}

function resolveConsultant(
  consultants: readonly ConsultantInfo[],
  param: string | undefined,
): ResolvedConsultant {
  const entry = consultants.find((c) => c.key === param);
  const key = entry?.key ?? "";
  const label = entry?.label ?? key;
  return { entry, key, label };
}

function consultantGate(
  loaded: boolean,
  loadFailed: boolean,
  entry: ConsultantInfo | undefined,
  param: string | undefined,
): ReactNode | null {
  if (loadFailed) {
    return (
      <ErrorBanner data-testid="explore-consultants-load-error">
        Could not load consultants.
      </ErrorBanner>
    );
  }
  if (!loaded) {
    return (
      <p className="text-sm text-text-muted" data-testid="explore-consultants-loading">
        Loading…
      </p>
    );
  }
  if (entry === undefined) {
    return (
      <div role="alert" className="rounded-md bg-banner-error-tint/15 px-3 py-2 text-sm text-banner-error-fg">
        Unknown consultant: {param}
      </div>
    );
  }
  return null;
}

export default function ExploreChat() {
  const { consultant } = useParams<{ consultant: string }>();
  const { consultants, loaded, loadFailed } = useConsultants();
  const { entry: consultantEntry, key: consultantKey, label: consultantLabel } = useMemo(
    () => resolveConsultant(consultants, consultant),
    [consultants, consultant],
  );

  const {
    reposLoaded,
    onlyDefaultRepo,
    soleRepoKey,
    showChooser,
    repos,
    selectedEngineKey,
    selectRepo,
  } = useRepoSelection();

  const resolvedEngineKey = resolveExploreEngineKey(
    selectedEngineKey,
    onlyDefaultRepo,
    soleRepoKey,
  );
  const create = useCallback(
    () => api.createExploreChatSession(consultantKey, resolvedEngineKey!),
    [consultantKey, resolvedEngineKey],
  );
  const enabled = exploreChatEnabled(consultantKey, reposLoaded, resolvedEngineKey);
  const { sessionId, bootstrapError, onCleared } = useUrlDraftSession(create, enabled);

  const gate = consultantGate(loaded, loadFailed, consultantEntry, consultant);
  if (gate !== null) return gate;

  if (bootstrapError !== null) {
    return (
      <div role="alert" className="rounded-md bg-banner-error-tint/15 px-3 py-2 text-sm text-banner-error-fg">
        Could not start explore session: {bootstrapError}
      </div>
    );
  }

  return (
    <>
      <RepoChooserGate
        show={showChooser}
        repos={repos}
        description="Choosing a repository starts an explore conversation for it."
        onSelect={selectRepo}
      />
      <ExploreChatSession
        consultantKey={consultantKey}
        consultantLabel={consultantLabel}
        sessionId={sessionId}
        onCleared={onCleared}
      />
    </>
  );
}
