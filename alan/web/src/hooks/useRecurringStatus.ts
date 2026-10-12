import type { RecurringStatus } from "../api";
import { useEngineResource, type UseEngineResourceResult } from "./useEngineResource";

export function useRecurringStatus(engineKey: string): UseEngineResourceResult<RecurringStatus> {
  return useEngineResource("recurringStatus", engineKey);
}
