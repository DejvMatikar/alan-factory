import { api } from "../../../api";
import { useDocQuery } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";

export type ProjectEngineKeyState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; engineKey: string | null | undefined };

export function useProjectEngineKey(projectId: string | undefined): ProjectEngineKeyState {
  const view = useDocQuery("project-engine", projectId, api.getProject);
  if (view.isError) return { status: "error", message: errorMessageOf(view.error) };
  if (view.data === undefined) return { status: "loading" };
  return { status: "ready", engineKey: view.data.engine_key };
}
