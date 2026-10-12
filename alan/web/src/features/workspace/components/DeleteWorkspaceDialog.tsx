import { useState } from "react";

import { api } from "../../../api";
import Button from "../../../components/Button";
import ShortcutHint from "../../../components/ShortcutHint";
import { errorMessageOf } from "../../../lib/errorMessage";
import { shortcutHint } from "../../../lib/shortcuts";
import DialogFrame from "../../../ui/DialogFrame";
import ErrorBanner from "../../../ui/ErrorBanner";
import { useAction } from "../../../ui/useAction";

export interface DeleteWorkspaceDialogProps {
  readonly taskId: string;
  readonly onClose: () => void;
  readonly onDeleted: () => void;
}

export default function DeleteWorkspaceDialog({
  taskId,
  onClose,
  onDeleted,
}: Readonly<DeleteWorkspaceDialogProps>) {
  const { busy, run } = useAction("rejection");
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = (): void => {
    setError(null);
    run(() => api.deleteWorkspace(taskId))
      .then(() => {
        onDeleted();
      })
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  return (
    <DialogFrame
      size="md"
      ariaLabel="Delete workspace?"
      testId="delete-dialog"
      busy={busy}
      onClose={onClose}
      onSubmit={handleConfirm}
    >
      <h3 className="text-base font-medium">Delete workspace?</h3>
      <p className="text-sm text-text-primary">
        This permanently removes the workspace folder under{" "}
        <code className="text-xs">~/.alan/workspaces/{taskId}</code>{" "}
        (handovers, invocations, history) and all queue rows for this task.
      </p>
      <p className="text-xs text-text-muted">
        Claude Code sessions under <code>~/.claude/projects/</code> are not
        removed.
      </p>
      {error !== null && <ErrorBanner>{error}</ErrorBanner>}
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="ghost" type="button" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="destroy"
          type="button"
          data-testid="delete-dialog-confirm"
          onClick={handleConfirm}
          disabled={busy}
        >
          {busy ? "Deleting…" : (
            <>
              Delete workspace
              <ShortcutHint {...shortcutHint("modal-confirm")} />
            </>
          )}
        </Button>
      </div>
    </DialogFrame>
  );
}
