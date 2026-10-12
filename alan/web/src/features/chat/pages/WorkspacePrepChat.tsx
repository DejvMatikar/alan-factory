import { useRef } from "react";
import { Link, useParams } from "react-router-dom";

import { api, type ChatMessage, type PrepChatBrief, type PrepChatKind } from "../../../api";
import { useDocQuery } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";
import ChatTranscript from "../components/ChatTranscript";
import MarkdownBody from "../../../components/MarkdownBody";
import { useConsultants } from "../../../hooks/useConsultants";

interface PrepChatData {
  kind: PrepChatKind;
  consultantKey: string | null;
  messages: ChatMessage[];
  brief: PrepChatBrief | null;
}

function conversationKindLabel(
  kind: PrepChatKind,
  consultantKey: string | null,
  labelOf: (key: string | null) => string | undefined,
): string {
  if (kind === "build") return "Build conversation";
  return `Explore conversation — ${labelOf(consultantKey) ?? consultantKey ?? "unknown consultant"}`;
}

async function fetchPrepChat(taskId: string): Promise<PrepChatData> {
  const transcript = await api.getWorkspacePrepChat(taskId);
  return {
    kind: transcript.kind,
    consultantKey: transcript.consultant_key ?? null,
    messages: transcript.messages,
    brief: transcript.brief ?? null,
  };
}

function PrepChatView({ taskId }: Readonly<{ taskId: string }>) {
  const view = useDocQuery("prep-chat", taskId, fetchPrepChat);
  const data = view.data ?? null;
  const loadError = view.isError ? errorMessageOf(view.error) : null;
  const { labelOf } = useConsultants();
  const scrollAnchorRef = useRef<HTMLDivElement>(null);

  const backLink = (
    <Link
      to={`/workspaces/${taskId}`}
      className="text-xs text-text-muted hover:underline hover:text-text-primary"
    >
      ← Back to workspace
    </Link>
  );

  if (loadError !== null) {
    return (
      <div className="space-y-3">
        <div
          role="alert"
          className="rounded-md bg-banner-error-tint/15 px-3 py-2 text-sm text-banner-error-fg"
        >
          Could not load preparation chat: {loadError}
        </div>
        {backLink}
      </div>
    );
  }

  if (data === null) {
    return (
      <p className="text-sm text-text-muted" data-testid="loading">
        Loading…
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-medium" data-testid="prep-chat-kind">
          {conversationKindLabel(data.kind, data.consultantKey, labelOf)}
        </h1>
        {backLink}
      </div>
      <output className="text-xs text-text-muted" data-testid="prep-chat-status">
        Archived — read-only
      </output>
      {data.brief !== null && (
        <section aria-label="Summary" data-testid="prep-chat-summary">
          <div
            className="w-full rounded-md border border-border bg-surface-elevated px-3 py-2 text-sm text-text-primary"
            data-testid="summary-preview"
          >
            <MarkdownBody measure="full">{data.brief.content}</MarkdownBody>
          </div>
        </section>
      )}
      <ChatTranscript
        scrollAnchorRef={scrollAnchorRef}
        messages={data.messages}
        toolActivity={[]}
        thinking={false}
        emptyHint="No messages recorded."
        activityVariant="overlay"
      />
    </div>
  );
}

export default function WorkspacePrepChat() {
  const { taskId } = useParams<{ taskId: string }>();

  if (taskId === undefined) {
    return (
      <p className="text-sm text-text-muted" data-testid="missing-task">
        No task id.
      </p>
    );
  }

  return <PrepChatView key={taskId} taskId={taskId} />;
}
