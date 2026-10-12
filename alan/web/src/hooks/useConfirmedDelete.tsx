import { useCallback, useState } from "react";

import ConfirmDestructiveDialog from "../components/ConfirmDestructiveDialog";
import { useDeletableDraft } from "./useDeletableDraft";

export interface UseConfirmedDeleteResult {
  requestDelete: () => void;
  deleting: boolean;
  deleteError: string | null;
  modal: React.ReactNode;
}

export function useConfirmedDelete(
  deleteCall: () => Promise<unknown>,
  onDeleted: () => void,
): UseConfirmedDeleteResult {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { deleteError, deleting, handleDelete } = useDeletableDraft(deleteCall, onDeleted);

  const requestDelete = useCallback(() => setConfirmOpen(true), []);

  const modal = confirmOpen ? (
    <ConfirmDestructiveDialog
      title="Delete this chat?"
      body="This chat and its transcript are removed permanently. This can't be undone."
      confirmLabel="Delete chat"
      onCancel={() => setConfirmOpen(false)}
      onConfirm={() => {
        setConfirmOpen(false);
        handleDelete();
      }}
    />
  ) : null;

  return { requestDelete, deleting, deleteError, modal };
}
