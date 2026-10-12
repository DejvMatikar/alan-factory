import { api, type ProjectDoc } from "../../../api";
import { useDocQuery, useResetDoc } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";

export type UseProjectDocResult =
  | { status: "loading" }
  | { status: "error"; message: string; refetch: () => void }
  | { status: "ready"; doc: ProjectDoc; refetch: () => void };

export function useProjectDoc(projectId: string | undefined): UseProjectDocResult {
  const view = useDocQuery("project", projectId, api.getProjectDoc);
  const refetch = useResetDoc("project", projectId);
  if (view.isError) return { status: "error", message: errorMessageOf(view.error), refetch };
  if (view.data === undefined) return { status: "loading" };
  return { status: "ready", doc: view.data, refetch };
}
