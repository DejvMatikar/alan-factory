import { useEffect, useLayoutEffect, useRef, useState } from "react";

interface StopWithDraftRestoreChat {
  interrupt: () => Promise<void>;
  restoredDraft: string | null;
  clearRestoredDraft: () => void;
}

export interface StopWithDraftRestoreResult {
  stopping: boolean;
  handleStop: () => void;
}

export function useStopWithDraftRestore(
  chat: StopWithDraftRestoreChat,
  draft: string,
  setDraft: (v: string) => void,
): StopWithDraftRestoreResult {
  const [stopping, setStopping] = useState(false);
  const draftRef = useRef(draft);
  useLayoutEffect(() => {
    draftRef.current = draft;
  });

  const { restoredDraft, clearRestoredDraft } = chat;
  useEffect(() => {
    if (restoredDraft === null) return;
    if (draftRef.current.trim() === "") {
      setDraft(restoredDraft);
    }
    clearRestoredDraft();
  }, [restoredDraft, clearRestoredDraft, setDraft]);

  const handleStop = (): void => {
    setStopping(true);
    void chat
      .interrupt()
      .catch(() => undefined)
      .finally(() => {
        setStopping(false);
      });
  };

  return { stopping, handleStop };
}
