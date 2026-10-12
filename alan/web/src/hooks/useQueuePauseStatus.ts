import { useHealthContext } from "../api/HealthProvider";
import type { SpendCapStop } from "../api/HealthProvider";

export interface QueuePauseStatus {
  queuePaused: boolean;
  pausedUntil: number | null;
  spendCapStop: SpendCapStop | null;
}

export function useQueuePauseStatus(): QueuePauseStatus {
  const { queuePaused, pausedUntil, spendCapStop } = useHealthContext();
  return { queuePaused, pausedUntil, spendCapStop: spendCapStop ?? null };
}
