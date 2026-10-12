import { useCallback, useEffect, useState, type ReactNode, type RefObject } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../../../api";
import { useDocQuery } from "../../../api/queries";
import type { AutoDispatchState } from "../hooks/autoDispatch";
import { resolveBuildEngineKey } from "../hooks/chatEngineBinding";
import { archivedTaskIdOf, isTurnRunning } from "../hooks/chatSessionReducer";
import AutoDispatchStatusRow from "../components/AutoDispatchStatusRow";
import ChatPanel, { type ChatPanelDeleteAction } from "../components/ChatPanel";
import ChatScaffold from "../components/ChatScaffold";
import ChatSummaryTab, { type SummaryMode } from "../components/ChatSummaryTab";
import EngineBadge from "../../../components/EngineBadge";
import { RepoChooserGate } from "../components/RepoChooserModal";
import { useChatEngineSelfCorrect } from "../../../shell/LegacyChatRedirect";
import { useAutoDispatchComposer, makeOnSummarized } from "../hooks/useAutoDispatchComposer";
import { useBuildChatSession, type UseBuildChatSessionResult } from "../hooks/useBuildChatSession";
import { useChatComposerActions, type ChatComposerActions } from "../hooks/useChatComposerActions";
import { useChatDelete } from "../../../hooks/useChatDelete";
import { useCollisionCheck, type CollisionInfo } from "../hooks/useCollisionCheck";
import { useDispatchProposalFlow } from "../hooks/useDispatchProposalFlow";
import { useDispatchWorkspace } from "../hooks/useDispatchWorkspace";
import { useProposePrefetch } from "../hooks/useProposePrefetch";
import { useProposeTrigger } from "../hooks/useProposeTrigger";
import { useRepoSelection } from "../hooks/useRepoSelection";
import { useRetryProvisioning } from "../hooks/useRetryProvisioning";
import { useStickToBottom } from "../hooks/useStickToBottom";
import { useUrlDraftSession } from "../hooks/useUrlDraftSession";

const PLAN_TEAM = "implementation_plan";

const BUILD_DOOR = {
  idleTestId: "build-plan",
  idleLabel: "Build plan",
  pendingLabel: "Preparing…",
  questionsTestId: "dispatch-build",
  questionsActionLabel: "Dispatch plan",
  questionsBusyLabel: "Dispatching…",
};

function roadmapDocKey(projectId: string | null, itemId: string | undefined): string | undefined {
  if (itemId === undefined) return undefined;
  return projectId ?? undefined;
}

// Best-effort label resolution only (P6.2 roadmap-item dispatch stamp): the raw
// id still displays via roadmapItemId when the lookup fails or hasn't landed.
function useRoadmapItemTitle(
  projectId: string | null,
  itemId: string | undefined,
): string | null {
  const { data, isError } = useDocQuery("project-roadmap", roadmapDocKey(projectId, itemId), api.getProject);
  if (isError || itemId === undefined) return null;
  return data?.roadmap?.find((r) => r.id === itemId)?.title ?? null;
}

function roadmapItemLabelFor(itemId: string | undefined, title: string | null): string | null {
  if (itemId === undefined) return null;
  return title ?? itemId;
}

const EMPTY_HINT =
  "Describe what you want built — the goal, the acceptance criteria, and the " +
  "boundaries. Summarize when ready, then dispatch a plan.";

type Tab = "chat" | "summary";

interface BuildChatContentProps {
  tab: Tab;
  scrollAnchorRef: RefObject<HTMLDivElement | null>;
  chat: UseBuildChatSessionResult;
  actions: ChatComposerActions;
  sessionReady: boolean;
  briefKey: string;
  summaryMode: SummaryMode;
  onSummaryModeChange: (mode: SummaryMode) => void;
  renderProposal: (liveBrief: string) => ReactNode;
  collisionInfo: CollisionInfo;
  onRerunCollision: (brief: string) => void;
  deleteAction: ChatPanelDeleteAction;
  onRetryProvisioning: () => void;
  onLiveBriefChange: (v: string) => void;
  onAutoDispatch: () => void;
  autoDispatch: AutoDispatchState;
  pinned: boolean;
  onJumpToLatest: () => void;
}

function BuildChatContent(props: Readonly<BuildChatContentProps>) {
  if (props.tab === "chat") {
    return (
      <ChatPanel
        scrollAnchorRef={props.scrollAnchorRef}
        chat={props.chat}
        actions={props.actions}
        emptyHint={EMPTY_HINT}
        sessionReady={props.sessionReady}
        rows={12}
        onRetryProvisioning={props.onRetryProvisioning}
        deleteAction={props.deleteAction}
        onAutoDispatch={props.onAutoDispatch}
        autoDispatch={props.autoDispatch}
        pinned={props.pinned}
        onJumpToLatest={props.onJumpToLatest}
      />
    );
  }
  return (
    <ChatSummaryTab
      key={props.briefKey}
      brief={props.chat.brief}
      onEdit={props.actions.handleBriefEdit}
      renderProposal={props.renderProposal}
      collisionInfo={props.collisionInfo}
      onRerunCollision={props.onRerunCollision}
      mode={props.summaryMode}
      onModeChange={props.onSummaryModeChange}
      onLiveBriefChange={props.onLiveBriefChange}
    />
  );
}

interface BuildChatSessionProps {
  sessionId: string | null;
  onCleared: (newId: string) => void;
  roadmapItemId?: string | undefined;
}

function BuildChatSession({
  sessionId,
  onCleared,
  roadmapItemId,
}: Readonly<BuildChatSessionProps>) {
  const [tab, setTab] = useState<Tab>("chat");
  const [summaryMode, setSummaryMode] = useState<SummaryMode>("preview");
  const [resumedOnMount] = useState(() => sessionId !== null);
  const [switchedToSessionId, setSwitchedToSessionId] = useState<string | null>(null);
  const chat = useBuildChatSession(sessionId);
  const navigate = useNavigate();
  useChatEngineSelfCorrect("build", chat.engineKey);
  const archivedTaskId = archivedTaskIdOf(chat.session);
  useEffect(() => {
    if (archivedTaskId !== null) {
      void navigate(`/workspaces/${archivedTaskId}/prep-chat`, { replace: true });
    }
  }, [archivedTaskId, navigate]);
  const handleRetryProvisioning = useRetryProvisioning(chat);
  const deleteFlow = useChatDelete(
    sessionId,
    api.interruptBuildChatSession,
    api.deleteBuildChatSession,
  );
  const roadmapItemTitle = useRoadmapItemTitle(chat.projectId, roadmapItemId);
  const roadmapItemLabel = roadmapItemLabelFor(roadmapItemId, roadmapItemTitle);
  const prefetch = useProposePrefetch(sessionId, chat.refreshRunSummary, {
    propose: api.proposeBuildDispatch,
    interrupt: api.interruptBuildChatSession,
  });
  const { reset: prefetchReset } = prefetch;
  const { anchorRef: scrollAnchorRef, pinned, jumpToLatest } = useStickToBottom([
    chat.messages,
    chat.toolActivity,
    isTurnRunning(chat.turn),
  ]);
  const { collisionInfo, rerun: rerunCollision } = useCollisionCheck("", {
    auto: false,
    chatFamily: "build",
    sessionId: sessionId ?? undefined,
    onSettled: () => {
      void chat.refreshRunSummary();
    },
  });

  const { dispatchWorkspace, dispatching, dispatchError } = useDispatchWorkspace(
    PLAN_TEAM,
    "build",
  );

  const flow = useDispatchProposalFlow({
    sessionId,
    engineKey: chat.engineKey,
    roadmapItemLabel,
    propose: api.proposeBuildDispatch,
    putBrief: api.putBuildChatBrief,
    dispatchWorkspace: (title, description, options) =>
      dispatchWorkspace(title, description, {
        ...options,
        projectId: chat.projectId,
        roadmapItemId,
        prepChatSessionId: sessionId,
      }),
    refreshRunSummary: chat.refreshRunSummary,
    workspaceDispatching: dispatching,
    workspaceDispatchError: dispatchError,
    door: BUILD_DOOR,
  });

  // The one place a fresh propose is triggered without an explicit click —
  // right after summarize settles, mirroring the eager prefetch.issue() call
  // right above it, so the Summary tab usually already has a settled
  // proposal by the time the person looks at it (2026-07-21 dispatch-flow
  // speed audit F7, extended to the inline panel).
  const { chatForComposer, triggerPropose } = useProposeTrigger(sessionId, chat, prefetch, flow);
  const actions = useChatComposerActions(
    chatForComposer,
    onCleared,
    makeOnSummarized(setTab, setSummaryMode, prefetch.issue, rerunCollision, triggerPropose),
  );

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

  const sessionReady = sessionId !== null;
  const briefKey = chat.brief?.updated_at ?? "no-brief";

  return (
    <>
      {deleteFlow.modal}
      {chat.engineKey != null && (
        <div className="flex justify-end" data-testid="build-chat-engine-header">
          <EngineBadge engineKey={chat.engineKey} testIdSuffix="build-chat-header" />
        </div>
      )}
      <ChatScaffold
        title="Build"
        tab={tab}
        onTab={setTab}
        runSummary={chat.runSummary}
        collisionRunSummary={chat.collisionRunSummary}
        focusHeadingKey={resumedOnMount ? sessionId : switchedToSessionId}
        autoDispatchStatus={
          <AutoDispatchStatusRow state={autoDispatch.state} onStop={autoDispatch.stop} />
        }
      >
        <BuildChatContent
          tab={tab}
          scrollAnchorRef={scrollAnchorRef}
          chat={chat}
          actions={composerActions}
          sessionReady={sessionReady}
          briefKey={briefKey}
          summaryMode={summaryMode}
          onSummaryModeChange={setSummaryMode}
          renderProposal={flow.renderProposal}
          collisionInfo={collisionInfo}
          onRerunCollision={(brief) => void rerunCollision(brief)}
          onRetryProvisioning={handleRetryProvisioning}
          deleteAction={{
            onRequestDelete: deleteFlow.requestDelete,
            deleting: deleteFlow.deleting,
            deleteError: deleteFlow.deleteError,
          }}
          onLiveBriefChange={handleLiveBriefChange}
          onAutoDispatch={handleAutoDispatch}
          autoDispatch={autoDispatch.state}
          pinned={pinned}
          onJumpToLatest={jumpToLatest}
        />
      </ChatScaffold>
    </>
  );
}

interface ProjectEngineLookup {
  readonly ready: boolean;
  // undefined = no project context at all; null = a project context exists but
  // its repository binding is unresolved (project not found, has no engine_key
  // of its own, or the lookup failed); a string is the resolved repository.
  readonly engineKey: string | null | undefined;
}

async function findProjectEngineKey(projectId: string): Promise<string | null> {
  const list = await api.listProjects();
  const found = list.find((p) => p.id === projectId);
  return found === undefined ? null : (found.engine_key ?? null);
}

// A build chat reached via ?project= (a repository-scoped entry point, e.g. a
// project's roadmap-item "Start work" link) already has its repository fixed by
// the project's own binding — this resolves that binding once so the eager
// session-create call passes the matching engine_key instead of drifting to the
// default and tripping the backend's project/engine-key mismatch guard. Unlike a
// fail-open default, an unresolved binding surfaces as null so the caller can show
// an explicit notice instead of silently creating a session against alan's own repo.
function resolvedProjectEngineKey(
  projectId: string | undefined,
  isError: boolean,
  data: string | null | undefined,
): string | null | undefined {
  if (projectId === undefined) return undefined;
  return isError ? null : data;
}

function useProjectRepoBinding(projectId: string | undefined): ProjectEngineLookup {
  const { data, isError } = useDocQuery("project-binding", projectId, findProjectEngineKey);
  return {
    ready: projectId === undefined || data !== undefined || isError,
    engineKey: resolvedProjectEngineKey(projectId, isError, data),
  };
}

function buildChatEnabled(
  reposLoaded: boolean,
  projectId: string | undefined,
  projectEngineReady: boolean,
  resolvedEngineKey: string | null,
): boolean {
  if (projectId !== undefined) return projectEngineReady && resolvedEngineKey !== null;
  return reposLoaded && resolvedEngineKey !== null;
}

function projectRepoUnresolved(
  projectId: string | undefined,
  projectEngineReady: boolean,
  resolvedEngineKey: string | null,
): boolean {
  return projectId !== undefined && projectEngineReady && resolvedEngineKey === null;
}

function UnresolvedRepoNotice() {
  return (
    <output className="block rounded-md bg-status-neutral-tint/15 px-3 py-2 text-sm text-status-neutral-fg">
      Repository unresolved — this project has no repository binding, so a build
      conversation can&apos;t be started.
    </output>
  );
}

export default function BuildChat() {
  const {
    reposLoaded,
    onlyDefaultRepo,
    soleRepoKey,
    showChooser,
    repos,
    selectedEngineKey,
    selectRepo,
  } = useRepoSelection();
  const [searchParams] = useSearchParams();
  const rawRoadmapItemId = searchParams.get("roadmapItem")?.trim();
  const roadmapItemId = rawRoadmapItemId === undefined || rawRoadmapItemId === "" ? undefined : rawRoadmapItemId;
  const projectId = searchParams.get("project") ?? undefined;
  const { ready: projectEngineReady, engineKey: projectEngineKey } = useProjectRepoBinding(projectId);

  const resolvedEngineKey = resolveBuildEngineKey({
    projectId,
    projectEngineKey,
    selectedEngineKey,
    onlyDefaultRepo,
    defaultKey: soleRepoKey,
  });

  const create = useCallback(() => {
    return api.createBuildChatSession(resolvedEngineKey!, projectId);
  }, [resolvedEngineKey, projectId]);
  const enabled = buildChatEnabled(reposLoaded, projectId, projectEngineReady, resolvedEngineKey);
  const { sessionId, bootstrapError, onCleared } = useUrlDraftSession(create, enabled);

  if (bootstrapError !== null) {
    return (
      <div role="alert" className="rounded-md bg-banner-error-tint/15 px-3 py-2 text-sm text-banner-error-fg">
        Could not start build session: {bootstrapError}
      </div>
    );
  }

  if (projectRepoUnresolved(projectId, projectEngineReady, resolvedEngineKey)) {
    return <UnresolvedRepoNotice />;
  }

  return (
    <>
      <RepoChooserGate
        show={showChooser}
        repos={repos}
        description="Choosing a repository starts a build conversation for it."
        onSelect={selectRepo}
      />
      <BuildChatSession
        sessionId={sessionId}
        onCleared={onCleared}
        roadmapItemId={roadmapItemId}
      />
    </>
  );
}
