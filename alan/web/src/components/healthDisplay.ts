import type { HealthState } from "../api";

export type HealthDisplayState = "healthy" | "degraded" | "paused" | "stale";

export interface HealthDisplay {
  state: HealthDisplayState;
  dot: string;
  ariaLabel: string;
  count: number | null;
}

export interface HealthDisplayInput {
  healthState: HealthState | null;
  queuePaused: boolean;
  blockedCount: number;
  needsUserCount: number;
}

const DOT_CLASS: Record<HealthDisplayState, string> = {
  healthy: "bg-green-500",
  degraded: "bg-red-500",
  paused: "bg-amber-400",
  stale: "bg-gray-400",
};

function issuesLabel(blockedCount: number, needsUserCount: number): string {
  const issueWord = blockedCount === 1 ? "issue" : "issues";
  const needsYouSuffix = needsUserCount > 0 ? ` · ${String(needsUserCount)} needs you` : "";
  return `${String(blockedCount)} ${issueWord}${needsYouSuffix}`;
}

function buildDisplay(
  state: HealthDisplayState,
  label: string,
  count: number | null,
): HealthDisplay {
  return { state, dot: DOT_CLASS[state], ariaLabel: `System health: ${label}`, count };
}

export function deriveHealthDisplay({
  healthState,
  queuePaused,
  blockedCount,
  needsUserCount,
}: HealthDisplayInput): HealthDisplay {
  if (healthState === null) {
    return buildDisplay("stale", "Status unknown", null);
  }
  if (blockedCount === 0 && !queuePaused) {
    return buildDisplay("healthy", "0 issues", null);
  }
  if (blockedCount === 0 && queuePaused) {
    return buildDisplay("paused", "Paused", null);
  }
  return buildDisplay("degraded", issuesLabel(blockedCount, needsUserCount), blockedCount);
}
