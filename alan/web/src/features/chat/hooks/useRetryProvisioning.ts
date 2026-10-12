import { useCallback } from "react";

import type { ChatMessage } from "../../../api";

interface RetryableChat {
  messages: ChatMessage[];
  sendMessage: (content: string) => Promise<void>;
}

export function useRetryProvisioning(chat: RetryableChat): () => void {
  return useCallback((): void => {
    const lastUser = [...chat.messages].reverse().find((m) => m.role === "user");
    if (lastUser === undefined) return;
    /* v8 ignore next */
    chat.sendMessage(lastUser.content).catch(() => undefined);
  }, [chat]);
}
