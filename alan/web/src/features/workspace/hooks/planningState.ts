import type { PlanningState } from "../../../api";

export const PLANNING_STATE_LEGEND = "D = Detailed plan · O = Outline";

export function planningStateLabel(state: PlanningState): string {
  return state === "detailed" ? "Detailed plan" : "Outline";
}

export function planningStateGlyph(state: PlanningState): string {
  return state === "detailed" ? "D" : "O";
}
