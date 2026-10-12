import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../../api";
import ChecklistRow from "../components/ChecklistRow";
import MutationErrorNotice from "../../../components/wizard/MutationErrorNotice";
import WizardFooter from "../components/WizardFooter";
import { classifyMutationError, type MutationBlock } from "../../../lib/connectFlowErrors";
import type { ConnectFlowState } from "../../../hooks/useConnectFlowState";
import { useReadinessPoll } from "../hooks/useReadinessPoll";
import { buildConnectStep3Rows, type ConnectStep3Row } from "./connectStep3Rows";

export interface ConnectStep3DispatchableProps {
  engineKey: string;
  state: ConnectFlowState;
  refetch: () => void;
}

interface ScaffoldActionResult {
  running: boolean;
  error: MutationBlock | null;
  trigger: () => void;
}

// Idempotent server-side (repo_scaffold.scaffold short-circuits on an
// already-persisted scaffold_pr_number) — this action is never disabled once
// a PR exists, so a re-tap is always safe to allow.
function useScaffoldAction(engineKey: string, refetch: () => void): ScaffoldActionResult {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<MutationBlock | null>(null);

  const trigger = (): void => {
    setRunning(true);
    setError(null);
    api.scaffoldEngine(engineKey).then(
      () => {
        setRunning(false);
        refetch();
      },
      (err: unknown) => {
        setRunning(false);
        setError(classifyMutationError(err, "scaffold"));
      },
    );
  };

  return { running, error, trigger };
}

// connectStep3Rows.ts always pairs a linkUrl with a linkLabel — never one
// without the other — so no further fallback text is needed here.
function RowLink({ row }: Readonly<{ row: ConnectStep3Row }>) {
  if (row.linkUrl === undefined) return null;
  return (
    <a
      href={row.linkUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="text-xs text-btn-action underline"
      data-testid={`step3-${row.key}-link`}
    >
      {row.linkLabel}
    </a>
  );
}

function scaffoldFooterCaption(engineKey: string, scaffoldPrNumber: number | null | undefined): string {
  if (scaffoldPrNumber == null) {
    return `Opens the scaffold pull request against ${engineKey}.`;
  }
  return `PR #${String(scaffoldPrNumber)} already open — tapping again adopts it, it never opens a second one.`;
}

// Step 3 — the checklist is evidence above the acts; the scaffold PR is the
// one thing the user can usefully fill; protection is alan's own act, stated
// as a consequence of merging, never a competing button (see the build brief
// for W2.3 / docs/architecture/connect_wizard.md). state/refetch come from
// the SAME useConnectFlowState call ConnectWizard already runs to select this
// step — a second independent call here would double every onboarding read
// (harmless in production, but a live scaffold-PR check is a real
// subprocess, and the e2e gh stub's PR-state queue is consumed per call).
export default function ConnectStep3Dispatchable({
  engineKey,
  state,
  refetch,
}: Readonly<ConnectStep3DispatchableProps>) {
  const readiness = state.status === "ready" ? state.readiness : null;
  const credentialPresent = state.status === "ready" && state.credentialPresent;
  const scaffold = useScaffoldAction(engineKey, refetch);
  const pollCaption = useReadinessPoll({
    engineKey,
    readiness,
    refetch,
    hasError: state.status === "error",
  });

  if (state.status === "loading") {
    return (
      <p className="text-sm text-text-muted" data-testid="step3-loading">
        Loading…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p role="alert" className="text-sm text-btn-destroy" data-testid="step3-error">
        {state.message}
      </p>
    );
  }
  if (readiness === null) {
    return (
      <p className="text-sm text-text-muted" data-testid="step3-no-readiness">
        Finish step 1 — register {engineKey} and add its credential — before this
        checklist can run.
      </p>
    );
  }

  const rows = buildConnectStep3Rows(readiness, credentialPresent);

  return (
    <div className="space-y-4" data-testid="connect-step3-dispatchable">
      <div className="space-y-2" data-testid="step3-checklist">
        {rows.map((row) => (
          <ChecklistRow
            key={row.key}
            label={row.label}
            status={row.status}
            detail={row.detail}
            remedy={row.remedy}
            action={<RowLink row={row} />}
            testId={`step3-${row.key}`}
          />
        ))}
      </div>

      <p className="text-xs text-text-muted" data-testid="step3-disclosure">
        Opens one pull request containing a stack-matched CI workflow, a Dependabot
        configuration, and a mechanically-assembled conventions document — three
        artifacts, one PR, no source files touched. The CI workflow's check names become
        this engine's authoritative checks.
      </p>

      {scaffold.error !== null && (
        <MutationErrorNotice block={scaffold.error} testId="step3-scaffold-error" />
      )}

      <WizardFooter
        primaryLabel="Open scaffold PR"
        onPrimary={scaffold.trigger}
        primaryDisabled={scaffold.running}
        caption={scaffoldFooterCaption(engineKey, readiness.state.scaffold_pr_number)}
        testId="step3-footer"
      />

      <p
        className="text-xs text-text-muted"
        role="status"
        aria-live="polite"
        data-testid="step3-poll-caption"
      >
        {pollCaption}
      </p>

      <p className="text-xs text-text-muted">
        <Link
          to="/build"
          className="-ml-1 inline-flex min-h-11 items-center px-1 text-btn-action underline"
          data-testid="step3-planning-link"
        >
          Start planning meanwhile
        </Link>{" "}
        — this creates nothing here; dispatch still needs this repo made ready first.
      </p>
    </div>
  );
}
