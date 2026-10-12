import { useCallback } from "react";

import { api, type StickyInstructionOrigin, type StickyInstructionOut } from "../../../api";
import { useDocQuery, useSetDocData } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";

export interface StickyInstructionState {
  text: string | null;
  origin: StickyInstructionOrigin | null;
  loading: boolean;
  error: string | null;
  clear: () => Promise<void>;
}

const STICKY_NAMESPACE = "sticky-instruction";
const CLEARED: StickyInstructionOut = { text: null, origin: null };

export function useStickyInstruction(taskId: string | undefined): StickyInstructionState {
  const view = useDocQuery(STICKY_NAMESPACE, taskId, (id) => api.getStickyInstructions(id));
  const setSticky = useSetDocData<StickyInstructionOut>(STICKY_NAMESPACE, taskId);

  const clear = useCallback(async () => {
    if (taskId === undefined) return;
    await api.clearStickyInstructions(taskId);
    setSticky(() => CLEARED);
  }, [taskId, setSticky]);

  return {
    text: view.data?.text ?? null,
    origin: view.data?.origin ?? null,
    loading: taskId !== undefined && view.data === undefined && !view.isError,
    error: view.isError ? errorMessageOf(view.error) : null,
    clear,
  };
}
