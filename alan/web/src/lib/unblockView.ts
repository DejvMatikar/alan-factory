import type { UnblockRecord } from "../api";

export const SILENCE_THRESHOLD_MINUTES = 90;
export const SILENCE_THRESHOLD_MS = SILENCE_THRESHOLD_MINUTES * 60 * 1000;

const HOST_OWNED_OUTCOMES: ReadonlySet<string> = new Set(["acted", "not_yet"]);

export function isHostOwnedOutcome(outcome: string): boolean {
  return HOST_OWNED_OUTCOMES.has(outcome);
}

function ageMs(record: UnblockRecord, nowMs: number): number {
  return nowMs - record.at * 1000;
}

export function isSilentTooLong(record: UnblockRecord, nowMs: number): boolean {
  return ageMs(record, nowMs) > SILENCE_THRESHOLD_MS;
}

export type UnblockOwner = "host" | "human";
export type UnblockDisplayState = "waiting" | "acted" | "needs_human";

export interface UnblockView {
  readonly owner: UnblockOwner;
  readonly displayState: UnblockDisplayState;
  readonly silent: boolean;
}

function displayStateFor(owner: UnblockOwner, outcome: string): UnblockDisplayState {
  if (owner === "human") return "needs_human";
  return outcome === "acted" ? "acted" : "waiting";
}

export function unblockView(record: UnblockRecord, nowMs: number): UnblockView {
  const hostOwnedOutcome = isHostOwnedOutcome(record.outcome);
  const silent = hostOwnedOutcome && isSilentTooLong(record, nowMs);
  const owner: UnblockOwner = hostOwnedOutcome && !silent ? "host" : "human";
  const displayState = displayStateFor(owner, record.outcome);
  return { owner, displayState, silent };
}

export function isHumanOwnedPark(
  record: UnblockRecord | null | undefined,
  nowMs: number,
): boolean {
  return record == null ? true : unblockView(record, nowMs).owner === "human";
}

const AUTHORITY_HOLDER_LABELS: Readonly<Record<string, string>> = {
  human_operator: "you, the human operator",
  fleet_grant_missing: "fleet configuration — the acting team lacks a dispatch grant",
  nobody: "no one yet — the host's own action didn't change anything",
};

export function authorityHolderLabel(holder: string | null | undefined): string {
  if (holder == null) return "a human";
  return AUTHORITY_HOLDER_LABELS[holder] ?? holder;
}
