import type { RefObject } from "react";

import type { ChatMessage, RepoProvisionStatus } from "../../../api";
import type { AutoDispatchState } from "../hooks/autoDispatch";
import { chatErrorOf, isTurnRunning, type ChatTurn } from "../hooks/chatSessionReducer";
import { JUMP_TO_LATEST_CLASS, SCROLL_BODY_FLOW_CLASS } from "../../../shell/stickyOffsets";
import ErrorBanner from "../../../ui/ErrorBanner";
import type { ChatComposerActions } from "../hooks/useChatComposerActions";
import type { ToolActivity } from "../hooks/useChatSessionGeneric";
import Button from "../../../components/Button";
import ChatComposer from "./ChatComposer";
import ChatTranscript from "./ChatTranscript";

interface ChatPanelChat {
  messages: ChatMessage[];
  toolActivity: ToolActivity[];
  turn: ChatTurn;
  queuedMessage: string | null;
  cancelQueued: () => void;
  repoProvision: RepoProvisionStatus | null;
}

export interface ChatPanelDeleteAction {
  onRequestDelete: () => void;
  deleting: boolean;
  deleteError: string | null;
}

interface ChatPanelProps {
  scrollAnchorRef: RefObject<HTMLDivElement | null>;
  chat: ChatPanelChat;
  actions: ChatComposerActions;
  emptyHint: string;
  sessionReady: boolean;
  rows?: number;
  onRetryProvisioning?: () => void;
  deleteAction?: ChatPanelDeleteAction;
  onAutoDispatch: () => void;
  autoDispatch: AutoDispatchState;
  pinned: boolean;
  onJumpToLatest: () => void;
}

function RepoProvisionBanner({
  status,
  onRetry,
}: Readonly<{ status: RepoProvisionStatus | null; onRetry: (() => void) | undefined }>) {
  if (status === "pending") {
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="repo-provision-pending"
        className="rounded-md border border-border bg-surface-elevated px-3 py-2 text-xs text-text-muted"
      >
        Provisioning the project&rsquo;s repository…
      </div>
    );
  }
  if (status === "error") {
    return (
      <ErrorBanner
        data-testid="repo-provision-error"
        action={
          onRetry !== undefined ? (
            <Button variant="ghost" onClick={onRetry} data-testid="repo-provision-retry">
              Retry
            </Button>
          ) : null
        }
      >
        Could not access the project&rsquo;s repository.
      </ErrorBanner>
    );
  }
  return null;
}

export default function ChatPanel({
  scrollAnchorRef,
  chat,
  actions,
  emptyHint,
  sessionReady,
  rows = 6,
  onRetryProvisioning,
  deleteAction,
  onAutoDispatch,
  autoDispatch,
  pinned,
  onJumpToLatest,
}: Readonly<ChatPanelProps>) {
  const deleteError = deleteAction?.deleteError ?? null;
  const thinking = isTurnRunning(chat.turn);
  const chatError = chatErrorOf(chat.turn);
  return (
    <>
      <div className={SCROLL_BODY_FLOW_CLASS}>
        <ChatTranscript
          scrollAnchorRef={scrollAnchorRef}
          messages={chat.messages}
          toolActivity={chat.toolActivity}
          thinking={thinking}
          emptyHint={emptyHint}
          queuedMessage={chat.queuedMessage}
          onCancelQueued={chat.cancelQueued}
        />
      </div>
      {!pinned && (
        <Button
          variant="ghost"
          onClick={onJumpToLatest}
          data-testid="chat-jump-to-latest"
          className={JUMP_TO_LATEST_CLASS}
        >
          Jump to latest
        </Button>
      )}
      <RepoProvisionBanner status={chat.repoProvision} onRetry={onRetryProvisioning} />
      {chatError !== null && <ErrorBanner>{chatError}</ErrorBanner>}
      {actions.summarizeError !== null && <ErrorBanner>{actions.summarizeError}</ErrorBanner>}
      {deleteError !== null && <ErrorBanner>{deleteError}</ErrorBanner>}
      <ChatComposer
        draft={actions.draft}
        setDraft={actions.setDraft}
        onSubmit={actions.handleSend}
        disabled={!sessionReady}
        onClear={actions.handleClear}
        onSummarize={actions.handleSummarize}
        onStop={actions.handleStop}
        summarizing={actions.summarizing}
        sessionReady={sessionReady}
        thinking={thinking}
        stopping={actions.stopping}
        rows={rows}
        deleteAction={deleteAction}
        onAutoDispatch={onAutoDispatch}
        autoDispatch={autoDispatch}
      />
    </>
  );
}
