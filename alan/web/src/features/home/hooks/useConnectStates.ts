import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  type EngineSummary,
  type ProjectListEntry,
  type SecretRecordResponse,
  type WorkspaceSummary,
} from "../../../api";
import { useSettingsUi } from "../../../shell/SettingsUiProvider";
import { credentialRecordName, hasTransportRecord } from "../../../hooks/useConnectFlowState";
import { useControlTokenPresent } from "../../../hooks/useControlTokenPresent";
import { deriveCompletion } from "../../../lib/wizardStepSelection";
import { hostSetupMessage, isMidSetup, operationalEngineKeys, resumeStepNumber } from "./connectState";

export interface ResumeEntry {
  readonly owner: string;
  readonly repo: string;
  readonly step: number;
}

export interface UseConnectStatesResult {
  readonly hostSetupMessage: string | null;
  readonly resumes: readonly ResumeEntry[];
}

export interface UseConnectStatesInput {
  readonly engines: readonly EngineSummary[];
  readonly workspaces: readonly WorkspaceSummary[] | null;
  readonly projects: readonly ProjectListEntry[] | null;
  readonly defaultEngineKey: string | null;
}

function engineSlug(engine: EngineSummary): string {
  return `${engine.repo.owner}/${engine.repo.repo}`;
}

function nonDefaultEnginesOf(
  engines: readonly EngineSummary[],
  defaultKey: string | null,
): readonly EngineSummary[] {
  return engines.filter((engine) => engineSlug(engine) !== defaultKey);
}

function compareResumeEntries(a: ResumeEntry, b: ResumeEntry): number {
  return `${a.owner}/${a.repo}`.localeCompare(`${b.owner}/${b.repo}`);
}

// A repo whose onboarding read rejects (a transient 500, or the same 422
// "no forge configured" a forge-less engine can carry) is omitted entirely —
// fail-quiet, never fail-nag with a line this component can't back up.
async function resumeEntryFor(
  engine: EngineSummary,
  secrets: readonly SecretRecordResponse[],
): Promise<ResumeEntry | null> {
  const { owner, repo } = engine.repo;
  let readiness;
  try {
    readiness = await api.getEngineOnboarding(engineSlug(engine));
  } catch {
    return null;
  }
  const completion = deriveCompletion({
    registered: true,
    credentialPresent: secrets.some((secret) => secret.name === credentialRecordName(owner, repo)),
    readiness,
  });
  if (!isMidSetup(completion)) return null;
  return { owner, repo, step: resumeStepNumber(completion) };
}

export function useConnectStates({
  engines,
  workspaces,
  projects,
  defaultEngineKey,
}: UseConnectStatesInput): UseConnectStatesResult {
  const tokenPresent = useControlTokenPresent();
  const { closedCount } = useSettingsUi();
  // Tri-state: null means "not yet fetched, or the last fetch failed" — a
  // failed listSecrets() leaves this at its previous value rather than
  // resetting it, so a transient error never flips a definitively-known
  // "present" back to "unknown".
  const [transportPresent, setTransportPresent] = useState<boolean | null>(null);
  const [resumes, setResumes] = useState<readonly ResumeEntry[]>([]);
  const mountedRef = useRef(true);
  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );

  // `engines` gets a brand-new array reference on every "engines" SSE tick
  // (useEngines() always sets a freshly parsed response), so reacting to it
  // directly IS reacting to that change stream — a second, independent
  // useChangeRefetch("engines", ...) subscription here would instead risk
  // firing before useEngines()'s own fetch for the same tick has resolved,
  // reading a stale (possibly empty) engine list and, since nothing would
  // ever re-invoke it once `engines` later updated, permanently stranding
  // Home on a stale resume list.
  const latestCallRef = useRef(0);

  // Signature, not the raw arrays: `workspaces`/`projects` get a fresh array
  // reference on every "workspace_list" SSE tick even when their content is
  // unchanged, and depending on the arrays directly would re-run the whole
  // N-engine onboarding fan-out below on every tick during a dispatch. Gated
  // on projects === null only (never workspaces): projects polls on every
  // Home tab, but the live workspace bucket is null for the whole session on
  // the Archived tab — gating on both would blank every resume line,
  // including the mid-setup population this hook must keep serving.
  const operationalSignature = useMemo(
    () => (projects === null ? null : operationalEngineKeys(workspaces ?? [], projects).join("\n")),
    [workspaces, projects],
  );
  const candidates = useMemo(() => {
    if (operationalSignature === null) return null;
    const operational = new Set(operationalSignature === "" ? [] : operationalSignature.split("\n"));
    return nonDefaultEnginesOf(engines, defaultEngineKey).filter(
      (engine) => !operational.has(engineSlug(engine)),
    );
  }, [engines, operationalSignature, defaultEngineKey]);

  const loadFor = useCallback((nonDefaultEngines: readonly EngineSummary[]) => {
    const callId = (latestCallRef.current += 1);
    void (async () => {
      let secrets: SecretRecordResponse[];
      try {
        secrets = await api.listSecrets();
      } catch {
        return;
      }
      // Only the most-recently-STARTED call is ever allowed to commit state —
      // guards against a slower, stale call's response landing after (and
      // clobbering) a fresher one's.
      if (!mountedRef.current || callId !== latestCallRef.current) return;
      setTransportPresent(hasTransportRecord(secrets));
      const entries = await Promise.all(
        nonDefaultEngines.map((engine) => resumeEntryFor(engine, secrets)),
      );
      if (!mountedRef.current || callId !== latestCallRef.current) return;
      setResumes(
        entries
          .filter((entry): entry is ResumeEntry => entry !== null)
          .sort(compareResumeEntries),
      );
    })();
  }, []);

  useEffect(() => {
    if (candidates !== null) loadFor(candidates);
  }, [candidates, loadFor]);

  // The effect above already covers every engine-list/operational-set
  // change; this one fires only on an actual Settings-close (closedCount
  // incrementing) — a secret write emits no SSE event of its own, so this is
  // the only signal that a credential may have changed. Routed through the
  // same filtered `candidates` — an unfiltered re-fan-out here would
  // resurrect a suppressed nag for an operational engine after any Settings
  // visit.
  const closedCountRef = useRef(closedCount);
  useEffect(() => {
    if (closedCountRef.current === closedCount) return;
    closedCountRef.current = closedCount;
    if (candidates !== null) loadFor(candidates);
  }, [closedCount, candidates, loadFor]);

  // An unresolved transport state (never fetched, or the last fetch failed)
  // must never be read as "definitively missing" — that would put a "forge
  // credential not set" clause in front of the user for a fact the client
  // doesn't actually know. Treat unknown as present so the clause is omitted
  // rather than asserted on a guess.
  return {
    hostSetupMessage: hostSetupMessage({ tokenPresent, transportPresent: transportPresent ?? true }),
    resumes,
  };
}
