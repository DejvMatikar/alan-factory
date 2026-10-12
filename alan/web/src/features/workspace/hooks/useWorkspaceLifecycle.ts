import { useCallback, useReducer, useState } from "react";

import { api, type WorkspaceDetail } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import { useAction } from "../../../ui/useAction";
import type { StopResumeMode } from "../components/WorkspaceHeaderActions";

export function stopResumeModeFor(ws: WorkspaceDetail): StopResumeMode | null {
  if (ws.status === "paused") return "resume";
  if (ws.status === "awaiting_input" && ws.has_resumable_failed_job) return "requeue";
  if (ws.status === "active" && ws.has_running_job) return "stop";
  return null;
}

type DeleteDialogAction = "open" | "close";

function deleteDialogReducer(_state: boolean, action: DeleteDialogAction): boolean {
  return action === "open";
}

export interface WorkspaceLifecycle {
  readonly toggleBusy: boolean;
  readonly toggleError: string | null;
  readonly pendingIsArchived: boolean;
  readonly handleToggle: () => void;
  readonly stopResumeMode: StopResumeMode | null;
  readonly stopResumeBusy: boolean;
  readonly stopResumeError: string | null;
  readonly handleStopResume: () => void;
  readonly deleteOpen: boolean;
  readonly openDelete: () => void;
  readonly closeDelete: () => void;
}

export function useWorkspaceLifecycle(
  taskId: string,
  ws: WorkspaceDetail,
  onChanged: () => void,
): WorkspaceLifecycle {
  const [deleteOpen, dispatchDelete] = useReducer(deleteDialogReducer, false);
  const [pendingIsArchived, setPendingIsArchived] = useState(false);
  const [toggleError, setToggleError] = useState<string | null>(null);
  const [stopResumeError, setStopResumeError] = useState<string | null>(null);
  const toggleAction = useAction();
  const stopResumeAction = useAction();

  const handleToggle = useCallback(() => {
    const isArchived = ws.status === "archived";
    setPendingIsArchived(isArchived);
    setToggleError(null);
    toggleAction
      .run(() => (isArchived ? api.unarchiveWorkspace(taskId) : api.archiveWorkspace(taskId)))
      .then(() => {
        onChanged();
      })
      .catch((err: unknown) => {
        setToggleError(errorMessageOf(err));
      });
  }, [taskId, ws.status, onChanged, toggleAction]);

  const stopResumeMode = stopResumeModeFor(ws);

  const handleStopResume = useCallback(() => {
    setStopResumeError(null);
    stopResumeAction
      .run(() =>
        stopResumeMode === "stop" ? api.stopWorkspace(taskId) : api.resumeWorkspace(taskId),
      )
      .then(() => {
        onChanged();
      })
      .catch((err: unknown) => {
        setStopResumeError(errorMessageOf(err));
      });
  }, [taskId, stopResumeMode, onChanged, stopResumeAction]);

  const openDelete = useCallback(() => {
    dispatchDelete("open");
  }, []);
  const closeDelete = useCallback(() => {
    dispatchDelete("close");
  }, []);

  return {
    toggleBusy: toggleAction.busy,
    toggleError,
    pendingIsArchived,
    handleToggle,
    stopResumeMode,
    stopResumeBusy: stopResumeAction.busy,
    stopResumeError,
    handleStopResume,
    deleteOpen,
    openDelete,
    closeDelete,
  };
}
