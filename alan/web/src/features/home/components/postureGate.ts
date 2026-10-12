import type { EnginePosture } from "../../../api";

export const POSTURE_RANK: Record<EnginePosture, number> = {
  observe: 0,
  propose: 1,
  self_merge: 2,
  autonomous: 3,
};

export const MENU_DISPATCH_MIN_POSTURE: EnginePosture = "autonomous";

export type MaintenanceGate =
  | { readonly kind: "unresolved" }
  | { readonly kind: "permitted" }
  | { readonly kind: "gated"; readonly reason: string };

// Scoped to Maintenance, this row's one dispatching control — does not mirror
// alan/engine_config.py's _TEAM_MIN_POSTURE table. The server's admitted_teams
// stays the enforcing boundary (fail-closed); this is presentation only. A
// gated repository still renders Maintenance (as a link to the posture
// setting), so unlike the retired menu this never hides the control outright.
// "unresolved" is a presentation state only — it makes no permission claim,
// and must never be treated as either "permitted" or "gated" by a caller.
export function maintenanceGate(resolved: boolean, posture: EnginePosture | null): MaintenanceGate {
  if (!resolved) return { kind: "unresolved" };
  if (posture !== null && POSTURE_RANK[posture] >= POSTURE_RANK[MENU_DISPATCH_MIN_POSTURE]) {
    return { kind: "permitted" };
  }
  if (posture === null) {
    return {
      kind: "gated",
      reason: "Maintenance posture couldn't be read for this repository.",
    };
  }
  return {
    kind: "gated",
    reason: `Maintenance requires autonomous posture — this repository is at ${posture}.`,
  };
}

// repoKey is always "owner/repo" (see fold.ts's registeredRepoEntries), so
// the first "/" always splits it cleanly — no -1 guard needed.
export function posturePathFor(repoKey: string): string {
  const slashIndex = repoKey.indexOf("/");
  const owner = repoKey.slice(0, slashIndex);
  const repo = repoKey.slice(slashIndex + 1);
  return `/connect/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/posture`;
}
