import { useCallback, useRef } from "react";

import type { DispatchProposalResponse } from "../../../api";
import { isErrorWithMessagePrefix } from "../../../lib/errorMessage";

interface PrefetchEntry {
  snapshot: string;
  promise: Promise<DispatchProposalResponse>;
  result?: DispatchProposalResponse;
  errored: boolean;
  consumed: boolean;
}

export type ProposePrefetchOutcome =
  | { immediate: DispatchProposalResponse }
  | { pending: Promise<DispatchProposalResponse>; refreshOnSettle: boolean };

export interface ProposePrefetchDeps {
  propose: (sessionId: string) => Promise<DispatchProposalResponse>;
  interrupt: (sessionId: string) => Promise<unknown>;
}

export interface UseProposePrefetchResult {
  issue: (briefSnapshot: string) => void;
  consume: (sessionId: string, briefContent: string) => ProposePrefetchOutcome;
  cancelBeforeLockingAction: <T>(action: () => Promise<T>) => Promise<T>;
  discardStale: () => void;
  reset: () => void;
}

const INTERRUPT_RETRY_DELAYS_MS = [60, 120, 240];
const ACTION_RETRY_DELAYS_MS = [60, 120, 240];

function sleep(ms: number | undefined): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isSettled(entry: PrefetchEntry): boolean {
  return entry.result !== undefined || entry.errored;
}

function is409(err: unknown): boolean {
  return isErrorWithMessagePrefix(err, "409:");
}

// The session lock a prefetch turn holds server-side only clears once the
// turn's own process is registered and terminated, or the turn finishes on
// its own — a single interrupt call can silently no-op (pre-Popen 409), so
// this nudges it repeatedly until the prefetch entry actually settles.
async function cancelPrefetchLock(
  entry: PrefetchEntry,
  sessionId: string,
  interrupt: (sessionId: string) => Promise<unknown>,
): Promise<void> {
  for (const delay of INTERRUPT_RETRY_DELAYS_MS) {
    if (isSettled(entry)) return;
    await interrupt(sessionId).catch(() => undefined);
    if (isSettled(entry)) return;
    await sleep(delay);
  }
  await interrupt(sessionId).catch(() => undefined);
}

async function runAfterLockRelease<T>(action: () => Promise<T>, attempt = 0): Promise<T> {
  try {
    return await action();
  } catch (err: unknown) {
    if (attempt >= ACTION_RETRY_DELAYS_MS.length || !is409(err)) throw err;
    await sleep(ACTION_RETRY_DELAYS_MS[attempt]);
    return runAfterLockRelease(action, attempt + 1);
  }
}

function trackEntry(entry: PrefetchEntry, refreshRunSummary: () => Promise<void>): void {
  entry.promise
    .then((res) => {
      entry.result = res;
      void refreshRunSummary();
    })
    .catch(() => {
      entry.errored = true;
      void refreshRunSummary();
    })
    .catch(() => undefined);
}

function entryMatches(entry: PrefetchEntry, briefContent: string): boolean {
  return entry.snapshot === briefContent && !entry.errored && !entry.consumed;
}

function freshPropose(
  sessionId: string,
  propose: (sessionId: string) => Promise<DispatchProposalResponse>,
): Promise<DispatchProposalResponse> {
  return runAfterLockRelease(() => propose(sessionId));
}

export function useProposePrefetch(
  sessionId: string | null,
  refreshRunSummary: () => Promise<void>,
  deps: ProposePrefetchDeps,
): UseProposePrefetchResult {
  const entryRef = useRef<PrefetchEntry | null>(null);
  const { propose, interrupt } = deps;

  const issue = useCallback(
    (briefSnapshot: string): void => {
      if (sessionId === null || entryRef.current !== null) return;
      const entry: PrefetchEntry = {
        snapshot: briefSnapshot,
        promise: propose(sessionId),
        errored: false,
        consumed: false,
      };
      entryRef.current = entry;
      trackEntry(entry, refreshRunSummary);
    },
    [sessionId, refreshRunSummary, propose],
  );

  const consume = useCallback(
    (sid: string, briefContent: string): ProposePrefetchOutcome => {
      const entry = entryRef.current;
      if (entry !== null && entryMatches(entry, briefContent)) {
        // Marking the entry consumed (rather than clearing entryRef) keeps it
        // reachable for cancelBeforeLockingAction/discardStale, which may
        // still need to cancel this turn's server-side session lock even
        // after its result has been handed off.
        entry.consumed = true;
        // entry.promise is already tracked by trackEntry, whose own handler
        // fires refreshRunSummary once it settles — refreshing again here
        // would double the run-summary GET for one propose turn.
        return entry.result !== undefined
          ? { immediate: entry.result }
          : { pending: entry.promise, refreshOnSettle: false };
      }
      entryRef.current = null;
      if (entry === null) return { pending: freshPropose(sid, propose), refreshOnSettle: true };
      return {
        pending: cancelPrefetchLock(entry, sid, interrupt).then(() => freshPropose(sid, propose)),
        refreshOnSettle: true,
      };
    },
    [propose, interrupt],
  );

  // Not memoized with useCallback: its generic signature loses type
  // precision through useCallback's inference, and nothing depends on this
  // function's referential identity across renders.
  function cancelBeforeLockingAction<T>(action: () => Promise<T>): Promise<T> {
    const entry = entryRef.current;
    if (entry === null) return action();
    entryRef.current = null;
    if (sessionId === null) return action();
    return cancelPrefetchLock(entry, sessionId, interrupt).then(() => runAfterLockRelease(action));
  }

  const bestEffortDiscard = useCallback((): void => {
    const entry = entryRef.current;
    entryRef.current = null;
    if (entry === null) return;
    if (sessionId === null) return;
    void interrupt(sessionId).catch(() => undefined);
  }, [sessionId, interrupt]);

  return {
    issue,
    consume,
    cancelBeforeLockingAction,
    discardStale: bestEffortDiscard,
    reset: bestEffortDiscard,
  };
}
