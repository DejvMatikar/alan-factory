import type {
  UnblockActivityEvent,
  UnblockClearance,
  UnblockDiagnosis,
  UnblockScopedActivity,
  UnblockWorkspaceActivity,
} from "../../../api";
import type { ChipTone } from "../../../components/AggregateChip";

export interface UnblockerWorkspaceRow {
  readonly workspaceKey: string;
  readonly lastAt: string;
  readonly shape: string;
  readonly eventsTotal: number;
  readonly attemptsTotal: number;
  readonly attemptsCeiling: number;
  readonly clearance: UnblockClearance;
  readonly gaveUp: boolean;
  readonly issueUrl: string | null;
  readonly diagnosis: UnblockDiagnosis | null;
  readonly events: readonly UnblockActivityEvent[];
  readonly needsAttention: boolean;
}

export const GAVE_UP_TITLE =
  "The host gave up on this workspace: it reached the attempt ceiling, a human-only authority holder owns the park, or the park is terminal.";

const _CLEARANCE_CHIP: Record<UnblockClearance, { tone: ChipTone; label: string }> = {
  cleared: { tone: "green", label: "cleared" },
  still_stuck: { tone: "red", label: "still stuck" },
  unknown: { tone: "unknown", label: "workspace gone" },
};

export function clearanceChipOf(clearance: UnblockClearance): { tone: ChipTone; label: string } {
  return _CLEARANCE_CHIP[clearance];
}

export function attemptsLabel(row: UnblockerWorkspaceRow): string {
  return row.attemptsTotal === 0 ? "—" : `Attempt ${row.attemptsTotal} of ${row.attemptsCeiling}`;
}

export function eventsCutByCap(row: UnblockerWorkspaceRow): boolean {
  return row.events.length < row.eventsTotal;
}

function eventsByWorkspace(
  events: readonly UnblockActivityEvent[],
): Map<string, UnblockActivityEvent[]> {
  const groups = new Map<string, UnblockActivityEvent[]>();
  for (const event of events) {
    const bucket = groups.get(event.workspace_key);
    if (bucket === undefined) groups.set(event.workspace_key, [event]);
    else bucket.push(event);
  }
  return groups;
}

function rowOf(
  source: UnblockWorkspaceActivity,
  events: readonly UnblockActivityEvent[],
): UnblockerWorkspaceRow {
  return {
    workspaceKey: source.workspace_key,
    lastAt: source.last_at,
    shape: source.shape,
    eventsTotal: source.events_total,
    attemptsTotal: source.attempts_total,
    attemptsCeiling: source.attempts_ceiling,
    clearance: source.clearance,
    gaveUp: source.gave_up,
    issueUrl: source.issue_url,
    diagnosis: source.diagnosis,
    events,
    needsAttention: source.clearance === "still_stuck" || source.gave_up,
  };
}

function compareRows(a: UnblockerWorkspaceRow, b: UnblockerWorkspaceRow): number {
  if (a.needsAttention !== b.needsAttention) return a.needsAttention ? -1 : 1;
  return Date.parse(b.lastAt) - Date.parse(a.lastAt);
}

export function buildWorkspaceRows(activity: UnblockScopedActivity): UnblockerWorkspaceRow[] {
  const byWorkspace = eventsByWorkspace(activity.events);
  return activity.workspaces
    .map((source) => rowOf(source, byWorkspace.get(source.workspace_key) ?? []))
    .sort(compareRows);
}
