import { type ControlTokenStatus } from "../api";
import { useControlTokenStatusQuery, useSetControlTokenStatus } from "../api/queries";

export interface UseControlTokenStatusResult {
  status: ControlTokenStatus | null;
  error: boolean;
  // Lets a caller that just mutated the token (extend/rotate/revoke) seed the
  // fresh ControlTokenStatusResponse those endpoints already return straight
  // into the status query's cache, instead of discarding it and waiting on the
  // next poll tick or firing a redundant GET.
  applyStatus: (status: ControlTokenStatus) => void;
}

// GET /api/control-token/status is unauthenticated by design (the whole
// point is that a dead credential is still legible), so this hook never
// touches the local control-token header itself — it only polls the
// server-computed lifecycle projection.
export function useControlTokenStatus(): UseControlTokenStatusResult {
  const { data, isError } = useControlTokenStatusQuery();
  const applyStatus = useSetControlTokenStatus();
  return { status: data ?? null, error: isError, applyStatus };
}
