import { useState } from "react";
import type { FormEvent } from "react";

import { api, type EngineConfig } from "../../../api";
import Button from "../../../components/Button";
import ChecklistRow, { type ChecklistRowStatus } from "../components/ChecklistRow";
import MutationErrorNotice from "../../../components/wizard/MutationErrorNotice";
import WizardFooter from "../components/WizardFooter";
import { classifyMutationError, type MutationBlock, type MutationStage } from "../../../lib/connectFlowErrors";
import {
  parseRepoIdentity,
  repoIdentityErrorText,
  segmentCharsetErrorText,
  type RepoIdentityParse,
} from "../../../lib/repoUrlIdentity";
import { useConnectFlowState } from "../../../hooks/useConnectFlowState";

const STEP1_BASE_PRESET = "python";

const INPUT_CLASSES =
  "min-h-11 w-full rounded-md border border-border bg-surface px-3 py-1.5 text-sm " +
  "text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60";
const LABEL_CLASSES = "mb-1 block text-xs font-medium text-text-primary";

// Derived from the current input on every render rather than stored in
// state: a submit-time-only error (the prior shape) can neither be reached
// when the Continue button is disabled — which it is for any unparsable
// input, since HTML implicit Enter-to-submit is suppressed when the form's
// default button is disabled — nor does it clear itself when the user edits
// the field after a failed submit. Computing it live fixes both.
function identityErrorText(parsed: RepoIdentityParse): string | null {
  if (parsed.ok) {
    return segmentCharsetErrorText(parsed.owner, parsed.repo);
  }
  return parsed.reason === "empty" ? null : repoIdentityErrorText(parsed.reason);
}

export interface StepIdentity {
  owner: string;
  repo: string;
}

export interface ConnectStep1IdentifyProps {
  identity: StepIdentity | null;
  onIdentityKnown: (owner: string, repo: string) => void;
}

export default function ConnectStep1Identify({
  identity,
  onIdentityKnown,
}: Readonly<ConnectStep1IdentifyProps>) {
  if (identity === null) {
    return <IdentityEntryForm onSubmit={onIdentityKnown} />;
  }
  return <Step1Checklist owner={identity.owner} repo={identity.repo} />;
}

function IdentityEntryForm({
  onSubmit,
}: Readonly<{ onSubmit: (owner: string, repo: string) => void }>) {
  const [repoInput, setRepoInput] = useState("");

  const parsed = parseRepoIdentity(repoInput);
  const errorText = identityErrorText(parsed);

  const handleSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (!parsed.ok || errorText !== null) return;
    onSubmit(parsed.owner, parsed.repo);
  };

  return (
    <form className="space-y-3" onSubmit={handleSubmit} data-testid="connect-identity-form">
      <div>
        <label htmlFor="connect-repo-url" className={LABEL_CLASSES}>
          Repository
        </label>
        <input
          id="connect-repo-url"
          value={repoInput}
          onChange={(e) => {
            setRepoInput(e.target.value);
          }}
          placeholder="owner/repo or https://github.com/owner/repo"
          data-testid="connect-repo-url-input"
          className={INPUT_CLASSES}
        />
        {parsed.ok && errorText === null && (
          <p
            role="status"
            aria-live="polite"
            data-testid="connect-identity-resolved"
            className="mt-1 text-xs text-text-muted"
          >
            Will connect {parsed.owner}/{parsed.repo}
          </p>
        )}
      </div>
      <Button
        variant="action"
        type="submit"
        data-testid="connect-identity-continue"
        disabled={!parsed.ok || errorText !== null}
      >
        Continue
      </Button>
      {errorText !== null && (
        <p role="alert" className="text-xs text-btn-destroy" data-testid="connect-identity-error">
          {errorText}
        </p>
      )}
    </form>
  );
}

type Step1ActionResult = { ok: true } | { ok: false; stage: MutationStage; err: unknown };

async function runStep1Action(owner: string, repo: string): Promise<Step1ActionResult> {
  const slug = `${owner}/${repo}`;
  let baseline: EngineConfig;
  try {
    baseline = await api.getEnginePresetConfig(STEP1_BASE_PRESET, owner, repo);
  } catch (err) {
    return { ok: false, stage: "register", err };
  }
  try {
    await api.createEngine(baseline);
  } catch (err) {
    return { ok: false, stage: "register", err };
  }
  try {
    await api.proposeEngineConfig(slug, null);
  } catch (err) {
    return { ok: false, stage: "propose", err };
  }
  return { ok: true };
}

type ProposalStatus = "pending" | "pass" | "fail";

interface Step1ConnectResult {
  running: boolean;
  block: MutationBlock | null;
  proposalStatus: ProposalStatus;
  connect: () => void;
}

function useStep1Connect(owner: string, repo: string, refetch: () => void): Step1ConnectResult {
  const [running, setRunning] = useState(false);
  const [block, setBlock] = useState<MutationBlock | null>(null);
  const [proposalStatus, setProposalStatus] = useState<ProposalStatus>("pending");

  const connect = (): void => {
    setRunning(true);
    setBlock(null);
    void runStep1Action(owner, repo).then((result) => {
      setRunning(false);
      // Refetch unconditionally: a propose-stage failure still leaves the
      // register call's side effect (the engine now exists) real and
      // persisted, so the checklist must reflect it even when the overall
      // action reports failure.
      refetch();
      if (result.ok) {
        setProposalStatus("pass");
      } else {
        setProposalStatus(result.stage === "propose" ? "fail" : "pending");
        setBlock(classifyMutationError(result.err, result.stage));
      }
    });
  };

  return { running, block, proposalStatus, connect };
}

function credentialRemedyProps(status: ChecklistRowStatus, slug: string): { remedy?: string } {
  if (status !== "fail") return {};
  return { remedy: `Add a credential for ${slug} in Settings (⚙) → Credentials.` };
}

function footerCaption(slug: string, registered: boolean): string {
  if (registered) return `${slug} is already registered.`;
  return (
    `Registers ${slug} with alan and starts a read-only scan of its stack — ` +
    "nothing is pushed or changed in the repository."
  );
}

function Step1Checklist({ owner, repo }: Readonly<{ owner: string; repo: string }>) {
  const slug = `${owner}/${repo}`;
  const { state, refetch } = useConnectFlowState(owner, repo);
  const { running, block, proposalStatus, connect } = useStep1Connect(owner, repo, refetch);

  if (state.status === "loading") {
    return (
      <p className="text-sm text-text-muted" data-testid="step1-loading">
        Loading…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p role="alert" className="text-sm text-btn-destroy" data-testid="step1-error">
        {state.message}
      </p>
    );
  }

  const credentialStatus: ChecklistRowStatus = state.credentialPresent ? "pass" : "fail";
  const proposalRowStatus: ChecklistRowStatus =
    proposalStatus === "pending" ? "pending" : proposalStatus;

  return (
    <div className="space-y-4" data-testid="connect-step1-checklist">
      <div className="space-y-2">
        <ChecklistRow label="Identity" status="pass" detail={slug} testId="step1-identity" />
        <ChecklistRow
          label="Credential"
          status={credentialStatus}
          {...credentialRemedyProps(credentialStatus, slug)}
          testId="step1-credential"
        />
        <ChecklistRow label="Reachability" status={state.reachability} testId="step1-reachability" />
        <ChecklistRow label="Stack scan" status={proposalRowStatus} testId="step1-proposal" />
      </div>
      {block !== null && <MutationErrorNotice block={block} testId="step1-mutation-error" />}
      <WizardFooter
        primaryLabel="Connect repository"
        onPrimary={connect}
        primaryDisabled={running || state.registered}
        caption={footerCaption(slug, state.registered)}
        testId="step1-footer"
      />
    </div>
  );
}
