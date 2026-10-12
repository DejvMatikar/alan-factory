import { useCallback, useState } from "react";

import {
  api,
  ApiError,
  type OnboardingReadiness,
  type PreflightStatus,
  type SecretRecordResponse,
} from "../api";
import { errorMessageOf } from "../lib/errorMessage";
import { useChangeRefetch } from "../api/ServerEventsProvider";

const TRANSPORT_PURPOSE = "transport";

// Mirrors alan.secrets_store.record_name(slug, TRANSPORT_PURPOSE) — the record
// identity the user creates and the wizard displays (docs/architecture/connect_wizard.md).
export function credentialRecordName(owner: string, repo: string): string {
  return `${owner}/${repo}:${TRANSPORT_PURPOSE}`;
}

// "Is the host's forge credential set up at all" — any engine's transport
// record, not one specific repo's. Every secret record is engine-scoped
// (`{engine}:{purpose}`, alan/secrets_store.py:55) with no host-level record,
// so "no transport record exists at all" is the only faithful reading of
// this question that doesn't require picking one engine to key on.
export function hasTransportRecord(secrets: readonly SecretRecordResponse[]): boolean {
  return secrets.some((secret) => secret.name.endsWith(`:${TRANSPORT_PURPOSE}`));
}

export type ReachabilityStatus = PreflightStatus | "no_forge";

export interface ConnectFlowReady {
  registered: boolean;
  credentialPresent: boolean;
  reachability: ReachabilityStatus;
  readiness: OnboardingReadiness | null;
}

export type ConnectFlowState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | ({ status: "ready" } & ConnectFlowReady);

export interface UseConnectFlowStateResult {
  state: ConnectFlowState;
  refetch: () => void;
}

async function fetchRegistered(engineKey: string): Promise<boolean> {
  try {
    await api.getEngine(engineKey);
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return false;
    throw err;
  }
}

async function fetchCredentialPresent(owner: string, repo: string): Promise<boolean> {
  const secrets = await api.listSecrets();
  const name = credentialRecordName(owner, repo);
  return secrets.some((secret) => secret.name === name);
}

function pushRepositoryStatus(readiness: OnboardingReadiness): ReachabilityStatus {
  const finding = readiness.checklist.find((f) => f.check === "push_repository");
  return finding?.status ?? "unknown";
}

interface OnboardingFetch {
  reachability: ReachabilityStatus;
  readiness: OnboardingReadiness | null;
}

// Step 3's checklist and the shared step-selection mechanism both need the
// full OnboardingReadiness payload, not just the one push_repository status
// step 1 reads — fetched once here and carried on ConnectFlowReady so no
// second GET .../onboarding call is needed anywhere else in the wizard.
async function fetchOnboarding(
  engineKey: string,
  registered: boolean,
): Promise<OnboardingFetch> {
  if (!registered) return { reachability: "unknown", readiness: null };
  try {
    const readiness = await api.getEngineOnboarding(engineKey);
    return { reachability: pushRepositoryStatus(readiness), readiness };
  } catch (err) {
    if (err instanceof ApiError && err.status === 422) return { reachability: "no_forge", readiness: null };
    throw err;
  }
}

// Composes three ungated reads (GET /api/secrets, GET /api/engines/{o}/{r},
// GET /api/engines/{o}/{r}/onboarding) into the discriminated union the step-1
// checklist renders from — never client-stored, always re-derived from the
// server on mount and on every "engines" change-event tick (see
// docs/architecture/connect_wizard.md's re-entry rule).
export function useConnectFlowState(owner: string, repo: string): UseConnectFlowStateResult {
  const engineKey = `${owner}/${repo}`;
  const [state, setState] = useState<ConnectFlowState>({ status: "loading" });

  const load = useCallback((): void => {
    // A background refetch (SSE "engines" tick, visibilitychange, the
    // readiness poll) must not flash previously-ready consumers back through
    // "loading" — that transiently collapses ConnectWizard's derived
    // completion and unmounts step 3 mid-poll. Only the very first load (or a
    // retry after an error) shows the loading state; once ready, later loads
    // keep the last-known-ready snapshot visible until the new one lands.
    setState((prev) => (prev.status === "ready" ? prev : { status: "loading" }));
    void (async () => {
      try {
        const registered = await fetchRegistered(engineKey);
        const [credentialPresent, onboarding] = await Promise.all([
          fetchCredentialPresent(owner, repo),
          fetchOnboarding(engineKey, registered),
        ]);
        setState({
          status: "ready",
          registered,
          credentialPresent,
          reachability: onboarding.reachability,
          readiness: onboarding.readiness,
        });
      } catch (err) {
        setState({ status: "error", message: errorMessageOf(err) });
      }
    })();
  }, [engineKey, owner, repo]);

  useChangeRefetch("engines", load, true);

  return { state, refetch: load };
}
