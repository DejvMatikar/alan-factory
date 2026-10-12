import { useEffect, useRef, useState } from "react";

import { api, type OnboardingReadiness } from "../../../api";
import { isDispatchableReady } from "../../../lib/wizardStepSelection";

export const READINESS_POLL_BASE_MS = 20_000;
export const READINESS_POLL_MAX_MS = 60_000;

// Pure so the backoff shape is unit-testable without fake timers: reset to
// base on a healthy tick, double (capped) on a failed one.
export function nextPollDelay(currentMs: number, ok: boolean): number {
  if (ok) return READINESS_POLL_BASE_MS;
  return Math.min(currentMs * 2, READINESS_POLL_MAX_MS);
}

function scaffoldPrMerged(readiness: OnboardingReadiness): boolean {
  return readiness.checklist.some(
    (f) => f.check === "review_and_merge_scaffold_pr" && f.status === "pass",
  );
}

// The poll only runs while there is a live scaffold PR and dispatch isn't
// fully ready yet — once isDispatchableReady is true (merged + no deferred
// protection), every live subprocess this poll would trigger stops paying for
// itself.
function pollGateActive(readiness: OnboardingReadiness | null): boolean {
  if (readiness === null) return false;
  if (readiness.state.scaffold_pr_number === null) return false;
  return !isDispatchableReady(readiness);
}

export const READINESS_POLL_IDLE_CAPTION =
  "Merge the scaffold pull request to continue — nothing else to do here yet.";
export const READINESS_POLL_ACTIVE_CAPTION =
  "This browser checks the scaffold PR periodically and applies branch protection once it's merged — it stops if you close this tab, so come back to finish protection.";
export const READINESS_POLL_PROTECTING_CAPTION =
  "Scaffold PR merged — applying branch protection now.";

export interface UseReadinessPollArgs {
  engineKey: string;
  readiness: OnboardingReadiness | null;
  refetch: () => void;
  hasError: boolean;
}

// Client-driven: there is no server daemon and no PR-watching event (per
// docs/architecture/connect_wizard.md), so this hook alone owns detecting a
// merge and firing protect while step 3 is open.
export function useReadinessPoll({
  engineKey,
  readiness,
  refetch,
  hasError,
}: Readonly<UseReadinessPollArgs>): string {
  const delayRef = useRef(READINESS_POLL_BASE_MS);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const hasErrorRef = useRef(hasError);
  const protectingRef = useRef(false);
  const [protecting, setProtecting] = useState(false);
  const gateActive = pollGateActive(readiness);

  useEffect(() => {
    hasErrorRef.current = hasError;
  }, [hasError]);

  useEffect(() => {
    if (!gateActive) return undefined;

    const tick = (): void => {
      refetch();
      delayRef.current = nextPollDelay(delayRef.current, !hasErrorRef.current);
      timerRef.current = setTimeout(tick, delayRef.current);
    };
    timerRef.current = setTimeout(tick, delayRef.current);

    // The deterministic e2e seam: a spec flips visibility instead of racing
    // the setTimeout cadence, and this is genuinely correct behaviour too —
    // a backgrounded-then-foregrounded tab shouldn't wait out a stale delay.
    const onVisibilityChange = (): void => {
      if (!document.hidden) refetch();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      clearTimeout(timerRef.current);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      // Reset so a later re-entry into polling (gateActive false -> true,
      // e.g. after a transient fetch error or once dispatch briefly reads
      // ready then regresses) starts back at the base cadence rather than
      // resuming mid-backoff.
      delayRef.current = READINESS_POLL_BASE_MS;
    };
  }, [gateActive, refetch]);

  useEffect(() => {
    if (readiness === null) return;
    if (readiness.state.scaffold_pr_number === null) return;
    if (!scaffoldPrMerged(readiness)) return;
    if (isDispatchableReady(readiness)) return;
    if (protectingRef.current) return;
    // Set BEFORE awaiting: a concurrent effect run in the async gap (e.g. a
    // visibilitychange-triggered refetch landing mid-call) must not fire a
    // second protect for the same detected merge.
    protectingRef.current = true;
    setProtecting(true);
    void api.protectEngine(engineKey).finally(() => {
      // Reset unconditionally once settled — re-armable, so if protect only
      // partially succeeded (a transient gh api failure, not a permanent
      // admin-403), the next readiness tick that still reports unprotected
      // fires protect again rather than latching forever.
      protectingRef.current = false;
      setProtecting(false);
      refetch();
    });
  }, [readiness, engineKey, refetch]);

  if (protecting) return READINESS_POLL_PROTECTING_CAPTION;
  if (gateActive) return READINESS_POLL_ACTIVE_CAPTION;
  return READINESS_POLL_IDLE_CAPTION;
}
