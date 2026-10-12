import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useConfirmedDelete, type UseConfirmedDeleteResult } from "./useConfirmedDelete";

export type UseChatDeleteResult = UseConfirmedDeleteResult;

const chatDeleteAnnouncementListeners = new Set<(text: string) => void>();

function publishChatDeleteAnnouncement(text: string): void {
  chatDeleteAnnouncementListeners.forEach((listener) => {
    listener(text);
  });
}

function subscribeChatDeleteAnnouncement(listener: (text: string) => void): () => void {
  chatDeleteAnnouncementListeners.add(listener);
  return () => {
    chatDeleteAnnouncementListeners.delete(listener);
  };
}

export function ChatDeleteAnnouncementRegion() {
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => subscribeChatDeleteAnnouncement(setAnnouncement), []);

  return (
    <p aria-live="polite" role="status" className="sr-only" data-testid="chat-delete-announcement">
      {announcement}
    </p>
  );
}

export function useChatDelete(
  sessionId: string | null,
  interruptCall: (id: string) => Promise<unknown>,
  deleteCall: (id: string) => Promise<unknown>,
): UseChatDeleteResult {
  const navigate = useNavigate();
  const { owner, repo } = useParams<{ owner?: string; repo?: string }>();

  const composedDelete = useCallback(() => {
    /* v8 ignore next */
    if (sessionId === null) return Promise.resolve();
    return interruptCall(sessionId).then(() => deleteCall(sessionId));
  }, [sessionId, interruptCall, deleteCall]);

  const onDeleted = useCallback(() => {
    publishChatDeleteAnnouncement("Conversation deleted — showing the repository dashboard.");
    const target = owner !== undefined && repo !== undefined ? `/repos/${owner}/${repo}` : "/";
    Promise.resolve(navigate(target, { replace: true })).catch(
      /* v8 ignore next */
      () => undefined,
    );
  }, [navigate, owner, repo]);

  const confirmed = useConfirmedDelete(composedDelete, onDeleted);
  const { requestDelete: openConfirm } = confirmed;

  const requestDelete = useCallback(() => {
    if (sessionId === null) return;
    openConfirm();
  }, [sessionId, openConfirm]);

  return { ...confirmed, requestDelete };
}
