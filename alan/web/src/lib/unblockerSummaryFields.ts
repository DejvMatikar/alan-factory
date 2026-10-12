import type { UnblockActivityResponse } from "../api";
import type { ChipTone } from "../components/AggregateChip";

const _RED_OUTCOMES: ReadonlySet<string> = new Set(["failed", "needs_human"]);

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

export function humanize(token: string): string {
  return token.replaceAll("_", " ");
}

export function outcomeTone(outcome: string): ChipTone {
  return _RED_OUTCOMES.has(outcome) ? "red" : "unknown";
}

export interface UnblockerCounts {
  readonly events: number;
  readonly touched: number;
  readonly stuck: number;
  readonly gaveUp: number;
}

export function unblockerCounts(activity: UnblockActivityResponse): UnblockerCounts {
  return {
    events: activity.events_total,
    touched: activity.workspaces_touched,
    stuck: activity.still_stuck_workspaces,
    gaveUp: activity.gave_up_workspaces,
  };
}

export function unblockerDigestLine(counts: UnblockerCounts): string {
  const parts = [plural(counts.events, "event"), `${plural(counts.touched, "workspace")} touched`];
  if (counts.stuck > 0) parts.push(`${counts.stuck} still stuck`);
  if (counts.gaveUp > 0) parts.push(`${counts.gaveUp} gave up`);
  return parts.join(" · ");
}

export interface UnblockerCountEntry {
  readonly key: "events" | "touched" | "stuck" | "gave-up";
  readonly label: string;
  readonly value: number;
  readonly tone: ChipTone;
}

export function unblockerCountEntries(counts: UnblockerCounts): UnblockerCountEntry[] {
  if (counts.events === 0 && counts.touched === 0) return [];
  const entries: UnblockerCountEntry[] = [
    { key: "events", label: "Unblocker events", value: counts.events, tone: "unknown" },
    { key: "touched", label: "Workspaces touched", value: counts.touched, tone: "unknown" },
  ];
  if (counts.stuck > 0) {
    entries.push({ key: "stuck", label: "Still stuck", value: counts.stuck, tone: "red" });
  }
  if (counts.gaveUp > 0) {
    entries.push({ key: "gave-up", label: "Gave up", value: counts.gaveUp, tone: "red" });
  }
  return entries;
}

export function unblockerHealthHref(engineKey: string): string {
  return `/health?engine=${encodeURIComponent(engineKey)}`;
}
