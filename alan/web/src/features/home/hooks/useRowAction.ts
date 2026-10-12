import { useCallback, useState } from "react";

import { api, type WorkspaceStatus } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import { useAction } from "../../../ui/useAction";
import { classifyRowActionError, rowActionFor, type RowActionKind } from "../components/rowAction";

const ACTION_FOR_KIND: Record<RowActionKind, (taskId: string) => Promise<void>> = {
  stop: (taskId) => api.stopWorkspace(taskId),
  archive: (taskId) => api.archiveWorkspace(taskId),
  unarchive: (taskId) => api.unarchiveWorkspace(taskId),
};

export interface UseRowActionResult {
  readonly busy: boolean;
  readonly error: string | null;
  readonly run: () => void;
}

export function useRowAction(
  taskId: string,
  status: WorkspaceStatus,
  reload: () => void,
): UseRowActionResult {
  const { busy, run: runAction } = useAction();
  const [error, setError] = useState<string | null>(null);
  const kind = rowActionFor(status).kind;

  const run = useCallback((): void => {
    setError(null);
    void runAction(() => ACTION_FOR_KIND[kind](taskId))
      .catch((err: unknown) => {
        if (classifyRowActionError(err) === "benign") return;
        setError(errorMessageOf(err));
      })
      .finally(() => {
        reload();
      });
  }, [kind, taskId, reload, runAction]);

  return { busy, error, run };
}
