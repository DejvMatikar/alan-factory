import { api, type Handover } from "../../../api";
import { useDocQuery } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";

const EMPTY_HANDOVERS: readonly Handover[] = Object.freeze([]);

export interface UseHandoversResult {
  handovers: readonly Handover[];
  error: string | null;
}

export function useHandovers(
  taskId: string,
  invocations: readonly string[],
): UseHandoversResult {
  const view = useDocQuery("handovers", `${taskId}|${invocations.join(",")}`, () =>
    api.getHandovers(taskId),
  );
  return {
    handovers: view.data ?? EMPTY_HANDOVERS,
    error: view.isError ? errorMessageOf(view.error) : null,
  };
}

export function resolveHandoverTargetLabel(handover: Handover): string {
  if (handover.handover_type === "terminal") return "Completed";
  if (handover.handover_type === "escalation") return "Escalated";
  return handover.to_team_suggestions![0] ?? "await_human";
}

export function findHandoverFrom(
  handovers: readonly Handover[],
  invId: string,
): Handover | null {
  return handovers.find((h) => h.from_invocation_id === invId) ?? null;
}
