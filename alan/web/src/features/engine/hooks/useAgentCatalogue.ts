import { api, type AgentCatalogue, type AgentCatalogueSummary } from "../../../api";
import { useDocQuery, useResetDoc, type DocQueryView } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";

export type AgentDocState<T> =
  | { status: "loading" }
  | { status: "error"; message: string; refetch: () => void }
  | { status: "ready"; data: T; refetch: () => void };

function toState<T>(view: DocQueryView<T>, refetch: () => void): AgentDocState<T> {
  if (view.isError) return { status: "error", message: errorMessageOf(view.error), refetch };
  if (view.data === undefined) return { status: "loading" };
  return { status: "ready", data: view.data, refetch };
}

export function useAgentCatalogue(engineKey: string | undefined): AgentDocState<AgentCatalogue> {
  const view = useDocQuery("agent-catalogue", engineKey, api.getEngineAgents);
  const refetch = useResetDoc("agent-catalogue", engineKey);
  return toState(view, refetch);
}

export function useAgentCatalogueSummary(
  engineKey: string | undefined,
): AgentDocState<AgentCatalogueSummary> {
  const view = useDocQuery("agent-catalogue-summary", engineKey, api.getEngineAgentsSummary);
  const refetch = useResetDoc("agent-catalogue-summary", engineKey);
  return toState(view, refetch);
}
