import { useState, type FormEvent } from "react";

import type { SummarizeFanout } from "./autoDispatch";
import { errorMessageOf } from "../../../lib/errorMessage";
import { useStopWithDraftRestore } from "./useStopWithDraftRestore";

interface ComposerChat {
  sendMessage: (content: string) => Promise<void>;
  clear: () => Promise<string>;
  summarize: () => Promise<{ content: string }>;
  updateBrief: (content: string) => Promise<unknown>;
  interrupt: () => Promise<void>;
  restoredDraft: string | null;
  clearRestoredDraft: () => void;
}

export interface ChatComposerActions {
  draft: string;
  setDraft: (v: string) => void;
  summarizing: boolean;
  summarizeError: string | null;
  stopping: boolean;
  handleSend: (e: FormEvent) => void;
  handleClear: () => void;
  handleSummarize: () => void;
  handleBriefEdit: (content: string) => void;
  handleStop: () => void;
  runSummarize: () => Promise<SummarizeFanout | null>;
}

export function useChatComposerActions(
  chat: ComposerChat,
  onCleared: (newId: string) => void,
  onSummarized: (content: string) => SummarizeFanout | void,
  initialDraft = "",
): ChatComposerActions {
  const [draft, setDraft] = useState(initialDraft);
  const [summarizing, setSummarizing] = useState(false);
  const [summarizeError, setSummarizeError] = useState<string | null>(null);
  const { stopping, handleStop } = useStopWithDraftRestore(chat, draft, setDraft);

  const handleSend = (e: FormEvent): void => {
    e.preventDefault();
    const content = draft.trim();
    if (content.length === 0) return;
    setDraft("");
    /* v8 ignore next */
    void chat.sendMessage(content).catch(() => undefined);
  };

  const handleClear = (): void => {
    if (!globalThis.confirm("Clear chat and start a new session?")) return;
    /* v8 ignore start */
    void chat.clear().then((newId) => {
      onCleared(newId);
    });
    /* v8 ignore stop */
  };

  const runSummarize = (): Promise<SummarizeFanout | null> => {
    setSummarizeError(null);
    setSummarizing(true);
    return chat
      .summarize()
      .then((brief) => onSummarized(brief.content) ?? null)
      .catch((err: unknown) => {
        setSummarizeError(errorMessageOf(err));
        return null;
      })
      .finally(() => setSummarizing(false));
  };

  const handleSummarize = (): void => {
    void runSummarize();
  };

  const handleBriefEdit = (content: string): void => {
    /* v8 ignore next */
    void chat.updateBrief(content).catch(() => undefined);
  };

  return {
    draft,
    setDraft,
    summarizing,
    summarizeError,
    stopping,
    handleSend,
    handleClear,
    handleSummarize,
    handleBriefEdit,
    handleStop,
    runSummarize,
  };
}
