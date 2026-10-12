import { useCallback } from "react";

import { useActionMutation } from "../api/queries";
import { errorMessageOf } from "../lib/errorMessage";

export function useDeletableDraft(
  deleteCall: () => Promise<unknown>,
  onDeleted: () => void,
) {
  const { run, isPending, isSuccess, isError, error } = useActionMutation(deleteCall);

  const handleDelete = useCallback(() => {
    run({ onSuccess: () => onDeleted() });
  }, [onDeleted, run]);

  return {
    deleteError: isError ? errorMessageOf(error) : null,
    deleting: isPending || isSuccess,
    handleDelete,
  };
}
