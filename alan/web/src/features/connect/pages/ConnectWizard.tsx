import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";

import WizardRail from "../components/WizardRail";
import WizardStepShell from "../components/WizardStepShell";
import { useConnectFlowState } from "../../../hooks/useConnectFlowState";
import { WIZARD_STEPS, deriveCompletion, selectStep } from "../../../lib/wizardStepSelection";
import ConnectStep1Identify from "./ConnectStep1Identify";
import ConnectStep2Configure from "./ConnectStep2Configure";
import ConnectStep3Dispatchable from "./ConnectStep3Dispatchable";
import ConnectStep4Posture from "./ConnectStep4Posture";

const WIZARD_TITLE = "Connect a repository";

function ResumableWizard({
  owner,
  repo,
  step,
}: Readonly<{ owner: string; repo: string; step: string | undefined }>) {
  const navigate = useNavigate();
  const { state, refetch } = useConnectFlowState(owner, repo);
  const completion = deriveCompletion({
    registered: state.status === "ready" && state.registered,
    credentialPresent: state.status === "ready" && state.credentialPresent,
    readiness: state.status === "ready" ? state.readiness : null,
  });
  const currentKey = selectStep(WIZARD_STEPS, completion, step);

  // A :step deep link that names an unbuilt or unknown step (never one this
  // wizard itself produces) bounces back to the canonical bare URL, which
  // re-derives the correct step from the same server reads — this is the
  // only navigation this component ever does; the ordinary re-entry path
  // (no :step segment) never redirects, it just renders currentKey. Validity
  // depends only on step + WIZARD_STEPS, never on state, so this must not
  // wait on state.status reaching "ready" — an onboarding-read error must
  // still bounce an invalid step back to the canonical URL.
  useEffect(() => {
    if (step === undefined) return;
    const validOverride = WIZARD_STEPS.some((s) => s.key === step && s.built);
    if (!validOverride) {
      void navigate(`/connect/${owner}/${repo}`, { replace: true });
    }
  }, [step, navigate, owner, repo]);

  return (
    <WizardStepShell title={WIZARD_TITLE} rail={<WizardRail steps={WIZARD_STEPS} currentKey={currentKey} />}>
      {currentKey === "identify" && (
        <ConnectStep1Identify
          identity={{ owner, repo }}
          // identity is always already known here (it comes from the URL, not
          // the fresh-start form), so this required prop is never invoked.
          /* v8 ignore next */
          onIdentityKnown={() => undefined}
        />
      )}
      {currentKey === "configure" && <ConnectStep2Configure owner={owner} repo={repo} />}
      {currentKey === "dispatchable" && (
        <ConnectStep3Dispatchable engineKey={`${owner}/${repo}`} state={state} refetch={refetch} />
      )}
      {currentKey === "posture" && <ConnectStep4Posture engineKey={`${owner}/${repo}`} />}
    </WizardStepShell>
  );
}

// Route shell for both /connect (fresh-start, no identity yet) and
// /connect/:owner/:repo[/:step] (per-repo resumable, :step an override-only
// deep link) — see docs/architecture/connect_wizard.md's "Route shape and
// re-entry" and its step-selection contract. All four steps are built;
// selectStep can resolve to any of them.
export default function ConnectWizard() {
  const { owner, repo, step } = useParams<{ owner?: string; repo?: string; step?: string }>();
  const navigate = useNavigate();
  const identity = owner !== undefined && repo !== undefined ? { owner, repo } : null;

  if (identity === null) {
    return (
      <WizardStepShell title={WIZARD_TITLE} rail={<WizardRail steps={WIZARD_STEPS} currentKey="identify" />}>
        <ConnectStep1Identify
          identity={null}
          onIdentityKnown={(nextOwner, nextRepo) => {
            void navigate(`/connect/${nextOwner}/${nextRepo}`, { replace: true });
          }}
        />
      </WizardStepShell>
    );
  }

  return <ResumableWizard owner={identity.owner} repo={identity.repo} step={step} />;
}
