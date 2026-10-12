import type { FinisherFunnelCounts, HealthScopeWindows } from "../../../api";

export type MetricKey = Exclude<keyof HealthScopeWindows, "funnel">;

export interface MetricRowDef {
  readonly key: MetricKey;
  readonly label: string;
  readonly summary: string;
}

export interface MetricGroupDef {
  readonly title: string;
  readonly rows: readonly MetricRowDef[];
}

export interface FunnelStageDef {
  readonly key: keyof FinisherFunnelCounts;
  readonly label: string;
}

export interface CaveatDef {
  readonly id: string;
  readonly title: string;
  readonly text: string;
}

export const OUTCOME_FLOOR = 0.5;

export const FUNNEL_STAGES: readonly FunnelStageDef[] = [
  { key: "not_crashed", label: "Crash-free" },
  { key: "not_needed_human", label: "No human gate" },
  { key: "converged", label: "Converged" },
  { key: "merged_pr", label: "Merged" },
  { key: "dod_evidenced", label: "Evidenced" },
];

export const METRIC_GROUPS: readonly MetricGroupDef[] = [
  {
    title: "Delivery",
    rows: [
      {
        key: "outcome",
        label: "Outcome (finisher) rate",
        summary: "Share of dispatched tasks that ended in a merged, evidenced PR.",
      },
      {
        key: "autonomous",
        label: "Fully autonomous rate",
        summary: "Share of dispatched tasks that got there with no recorded human intervention.",
      },
      {
        key: "build_success",
        label: "Build success rate",
        summary: "Share of build workspaces that finished successfully in the window.",
      },
    ],
  },
  {
    title: "Quality",
    rows: [
      {
        key: "first_pass_approval",
        label: "First-pass approval rate",
        summary: "Share of implementation attempts approved on their first verification.",
      },
      {
        key: "unproven_clauses",
        label: "Unproven clauses",
        summary: "Share of acceptance clauses with no test or subtask evidence. Lower is better.",
      },
      {
        key: "format_error",
        label: "Format error rate",
        summary: "Share of first-attempt structured output the host rejected. Lower is better.",
      },
      {
        key: "correction_recovery",
        label: "Correction recovery rate",
        summary: "Share of rejected output that a second attempt corrected.",
      },
    ],
  },
  {
    title: "Unblocker",
    rows: [
      {
        key: "rescue_success",
        label: "Unblocker rescue success rate",
        summary: "Share of engaged parked workspaces the unblocker cleared.",
      },
      {
        key: "rescue_load",
        label: "Unblocker rescue load rate",
        summary: "Share of the fleet's workspaces the unblocker engaged with.",
      },
    ],
  },
];

export const CAVEATS: readonly CaveatDef[] = [
  {
    id: "comparison",
    title: "Reading 7 days against 30 days",
    text: "Each metric compares the last 7 days with the last 30 days. A difference within 5 percentage points reads as about the same. When either window has fewer than 10 samples its value is still shown, dimmed and marked low data, but no better or worse verdict is given. For the format error rate and the unproven-clause share, lower is better.",
  },
  {
    id: "coverage",
    title: "Windows that reach back less than they say",
    text: "A window only counts records that still exist. When the underlying records do not reach back the full 7 or 30 days, the value says how many days it actually covers instead of presenting a shorter period as the full window.",
  },
  {
    id: "outcome",
    title: "Outcome rate and its stages",
    text: "The outcome rate is the share of build tasks dispatched in the window that ended in a merged, evidenced PR. The stage rows show where tasks drop off, as a count out of the tasks dispatched in that window, and carry no verdict of their own. A rate below the 50% floor is flagged next to its value.",
  },
  {
    id: "autonomous",
    title: "Fully autonomous",
    text: "Counts tasks that reached a merged, evidenced outcome with zero recorded control-plane interventions and never ended waiting on a human. Work the autonomous unblocker rescued still counts as autonomous, because its answers and re-dispatches are not recorded as interventions. Rescues outside the control plane, such as manual PR merges or CI reruns, are not recorded either, so this is an upper bound on autonomy.",
  },
  {
    id: "build",
    title: "Build success",
    text: "Counts build workspaces by when they reached a finished state, inside each window. Archiving a workspace never changes the figure. Work still in progress is not counted, and non-build work such as research, brainstorming and architecture is excluded.",
  },
  {
    id: "unproven",
    title: "Unproven clauses",
    text: "The share of acceptance clauses in the window that have neither a bound test reference nor coverage by a subtask definition-of-done clause, out of all acceptance clauses in that window. Clauses a human has since withdrawn are excluded.",
  },
  {
    id: "formats",
    title: "Format errors and corrections",
    text: "Per-repository format error and correction recovery rates count only records that can be attributed to a repository. The fleet-wide rows count every record, including those that cannot be attributed.",
  },
  {
    id: "unblocker",
    title: "Unblocker rescue",
    text: "Success is the share of parked workspaces the unblocker engaged with in the window that it cleared rather than left stuck; a park it is still working is excluded until it clears or the host reports it. Load is the share of the fleet's current workspaces, across every team, that it engaged with at all in the window.",
  },
];
