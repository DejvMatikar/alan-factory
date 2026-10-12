import type { ProjectListEntry, WorkspaceSummary } from "../../../api";
import { WIZARD_STEPS, selectStep, type WizardStepCompletion } from "../../../lib/wizardStepSelection";

// Operational-evidence exclusion: a repository with a bound project OR a
// PR-bearing workspace has already been worked in, so the resume-line
// candidate filter (useConnectStates) drops it before the per-repo setup-state
// lookup even runs. This is a POPULATION filter, not a completion-contract
// change — isMidSetup below stays byte-identical, and this never reads
// intended_engine_key: intent is stamped by the wizard's own first finish, so
// keying on it would suppress the line for exactly the population it serves.
// The PR-bearing check is inlined (rather than calling
// connectStep4PostureLogic's hasPrBearingWorkspace per row) so the workspace
// list is scanned once instead of once per distinct engine key.
export function operationalEngineKeys(
  workspaces: readonly WorkspaceSummary[],
  projects: readonly ProjectListEntry[],
): readonly string[] {
  const keys = new Set<string>();
  for (const project of projects) {
    if (project.engine_key != null) keys.add(project.engine_key);
  }
  for (const workspace of workspaces) {
    if (workspace.engine_key != null && workspace.pr_number != null) keys.add(workspace.engine_key);
  }
  return [...keys].sort((a, b) => a.localeCompare(b));
}

// The item's written trigger ("GET .../onboarding overall !== pass") is
// factually unsatisfiable — build_checklist always includes
// _check_collaborator_access, hard-coded "unknown", so overall_status can
// never return "pass" and every connected repo would carry a permanent
// resume line forever. Corrected here: mid-setup is derived from the
// wizard's own completion contract (identify/dispatchable), the two terms
// deriveCompletion never hard-codes false — configure and posture are
// deliberately excluded (see docs/architecture/connect_wizard.md).
export function isMidSetup(completion: WizardStepCompletion): boolean {
  return !completion.identify || !completion.dispatchable;
}

// The label only — navigation always targets the bare /connect/:owner/:repo
// URL, which re-derives this exact step itself (ConnectWizard.tsx), so the
// two can never disagree about where the user lands.
export function resumeStepNumber(completion: WizardStepCompletion): number {
  const key = selectStep(WIZARD_STEPS, completion, undefined);
  return WIZARD_STEPS.findIndex((step) => step.key === key) + 1;
}

export interface HostSetupInputs {
  readonly tokenPresent: boolean;
  readonly transportPresent: boolean;
}

const HOST_SETUP_PREFIX = "Finish host setup — ";

function hostSetupClauses(inputs: HostSetupInputs): string[] {
  const clauses: string[] = [];
  if (!inputs.transportPresent) clauses.push("forge credential not set");
  if (!inputs.tokenPresent) clauses.push("control token not set");
  return clauses;
}

export function hostSetupMessage(inputs: HostSetupInputs): string | null {
  const clauses = hostSetupClauses(inputs);
  if (clauses.length === 0) return null;
  return `${HOST_SETUP_PREFIX}${clauses.join(" / ")}`;
}
