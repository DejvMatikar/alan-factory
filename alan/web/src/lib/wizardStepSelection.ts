import type { OnboardingReadiness } from "../api";

export interface WizardStepDefinition {
  key: string;
  label: string;
  built: boolean;
}

export const DISPATCHABLE_STEP_KEY = "dispatchable";

// The delivered step keys from ConnectWizard.tsx's original STEPS constant,
// reused verbatim (W2.1). "built" marks whether a step has a component to
// render yet — all four steps are now built (W2.2 completed the set), so
// selectStep can resolve to any of them.
export const WIZARD_STEPS: readonly WizardStepDefinition[] = [
  { key: "identify", label: "Identify", built: true },
  { key: "configure", label: "Configure", built: true },
  { key: DISPATCHABLE_STEP_KEY, label: "Make dispatchable", built: true },
  { key: "posture", label: "Posture", built: true },
];

export type WizardStepCompletion = Readonly<Record<string, boolean>>;

export interface WizardProgressInput {
  registered: boolean;
  credentialPresent: boolean;
  readiness: OnboardingReadiness | null;
}

// Step 3 done ⇔ scaffold PR merged ∧ protect() has actually run ∧ no deferred
// protection finding remains. protection_applied_keys/protection_deferred are
// BOTH empty before protect() ever runs (OnboardingState's own defaults), so
// "deferred is empty" alone can't distinguish "fully applied" from "never
// attempted" — the applied-keys check closes that gap. Shared with
// useReadinessPoll's gate (¬isDispatchableReady) so the two never drift out
// of sync on what "protected" means.
export function isDispatchableReady(readiness: OnboardingReadiness | null): boolean {
  if (readiness === null) return false;
  const scaffoldMerged = readiness.checklist.some(
    (f) => f.check === "review_and_merge_scaffold_pr" && f.status === "pass",
  );
  const protectionAttempted =
    readiness.state.protection_applied_keys.length > 0 ||
    readiness.state.protection_deferred.length > 0;
  return scaffoldMerged && protectionAttempted && readiness.state.protection_deferred.length === 0;
}

// configure is done once repo writes have started — there is no server-side
// "config saved" marker (OnboardingState carries none, EngineConfig has no
// saved-at field), so this derives from the same downstream signal that
// already proves step 2's PATCH must have run: either the scaffold PR
// exists, or dispatchable is already fully ready (which itself implies a
// scaffold PR was merged). A fresh page load re-running the proposal is the
// accepted cost of not minting a new backend marker for this M item.
export function isConfigureDone(readiness: OnboardingReadiness | null): boolean {
  return readiness !== null && (readiness.state.scaffold_pr_number !== null || isDispatchableReady(readiness));
}

// Step 1 completion excludes the stack-scan proposal's status on purpose —
// that's component-local state (ProposalStatus in ConnectStep1Identify), not
// server-derived, and step selection must never read client state.
export function deriveCompletion(input: WizardProgressInput): WizardStepCompletion {
  return {
    identify: input.registered && input.credentialPresent,
    configure: isConfigureDone(input.readiness),
    dispatchable: isDispatchableReady(input.readiness),
    posture: false,
  };
}

function deriveCurrentStep(
  steps: readonly WizardStepDefinition[],
  completion: WizardStepCompletion,
): string {
  const builtSteps = steps.filter((step) => step.built);
  const firstIncomplete = builtSteps.find((step) => completion[step.key] !== true);
  // WIZARD_STEPS always registers at least one built step ("identify"), so
  // builtSteps is never empty — indexing its last element is safe.
  return (firstIncomplete ?? builtSteps.at(-1)!).key;
}

// requestedKey is a deep-link override, honored only when it names a BUILT
// step — an unbuilt or unknown key (a stale bookmark, a typo) falls through
// to the derived current step instead of stranding the user on dead content.
export function selectStep(
  steps: readonly WizardStepDefinition[],
  completion: WizardStepCompletion,
  requestedKey: string | undefined,
): string {
  if (requestedKey !== undefined) {
    const requested = steps.find((step) => step.key === requestedKey);
    if (requested?.built) return requestedKey;
  }
  return deriveCurrentStep(steps, completion);
}
