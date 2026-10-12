import type { EnginePreflightFinding, OnboardingReadiness } from "../../../api";
import type { ChecklistRowStatus } from "../components/ChecklistRow";

export interface ConnectStep3Row {
  key: string;
  label: string;
  status: ChecklistRowStatus;
  detail?: string | undefined;
  remedy?: string | undefined;
  linkUrl?: string | undefined;
  linkLabel?: string | undefined;
}

// One shared label per branch_protection_compat.CompatibilityRow.key — both
// the applied and the deferred presentation reuse it, so the two never drift.
const PROTECTION_LABELS: Record<string, string> = {
  conversation_resolution: "Require conversation resolution before merging",
  required_approvals: "Required approvals set to zero",
  squash_merge_enabled: "Squash merging enabled",
  delete_branch_on_merge: "Delete head branches on merge",
  required_checks: "Required status checks include this repo's CI",
};

function protectionLabel(key: string): string {
  return PROTECTION_LABELS[key] ?? key;
}

function findByCheck(
  checklist: readonly EnginePreflightFinding[],
  check: string,
): EnginePreflightFinding | undefined {
  return checklist.find((f) => f.check === check);
}

// Repository reachability: a real act-now failure gets the danger chip, an
// unknown (no credential yet to try with) is honest neutral, never "fail".
function reachabilityRow(finding: EnginePreflightFinding | undefined): ConnectStep3Row {
  const label = "Repository reachability";
  if (finding === undefined || finding.status === "unknown") {
    return { key: "reachability", label, status: "unverifiable", detail: finding?.message };
  }
  if (finding.status === "fail") {
    return { key: "reachability", label, status: "fail", detail: finding.message, remedy: finding.remedy };
  }
  return { key: "reachability", label, status: "pass", detail: finding.message };
}

// Whether the stored credential can perform the one write the setup action
// always makes — the CI workflow file. Shaped like reachabilityRow: a real
// act-now failure gets the danger chip, an unknown (no credential yet, or a
// fine-grained token this probe can't read) is honest neutral, never "fail".
// Unlike reachabilityRow, the remedy is carried through on the unverifiable
// branch too — the fine-grained-token note is the only way that gap surfaces.
function workflowWriteRow(finding: EnginePreflightFinding | undefined): ConnectStep3Row {
  const label = "Workflow-file write permission";
  if (finding === undefined || finding.status === "unknown") {
    return {
      key: "workflow_write", label, status: "unverifiable",
      detail: finding?.message, remedy: finding?.remedy,
    };
  }
  if (finding.status === "fail") {
    return {
      key: "workflow_write", label, status: "fail",
      detail: finding.message, remedy: finding.remedy,
    };
  }
  return { key: "workflow_write", label, status: "pass", detail: finding.message };
}

// The one structural split the backend's single "fail" value hides: whether
// scaffold has been run at all (scaffold_pr_number === null) versus a PR that
// is open and simply waiting on the user to merge it.
function scaffoldRow(
  finding: EnginePreflightFinding | undefined,
  scaffoldPrNumber: number | null | undefined,
  scaffoldPrUrl: string | null | undefined,
): ConnectStep3Row {
  const label = "Scaffold pull request merged";
  const link = scaffoldPrUrl != null ? { linkUrl: scaffoldPrUrl, linkLabel: "View PR" } : {};
  if (finding === undefined || finding.status === "unknown") {
    return { key: "scaffold", label, status: "unverifiable", detail: finding?.message, ...link };
  }
  if (finding.status === "pass") {
    return { key: "scaffold", label, status: "pass", detail: finding.message, ...link };
  }
  if (scaffoldPrNumber == null) {
    return { key: "scaffold", label, status: "not_started", detail: finding.message };
  }
  return { key: "scaffold", label, status: "waiting", detail: finding.message, ...link };
}

// Structurally unverifiable — never rendered pass/fail, per the outcome
// contract this checklist reads from build_checklist's own hard-coded
// "unknown" status for this check.
function collaboratorRow(finding: EnginePreflightFinding | undefined): ConnectStep3Row {
  return {
    key: "reviewer_access", label: "Reviewer repo access", status: "unverifiable",
    detail: finding?.message, remedy: finding?.remedy,
  };
}

function protectionRows(readiness: OnboardingReadiness): ConnectStep3Row[] {
  const applied: ConnectStep3Row[] = readiness.state.protection_applied_keys.map((key) => ({
    key: `protection-${key}`,
    label: protectionLabel(key),
    status: "pass",
  }));
  const deferred: ConnectStep3Row[] = readiness.state.protection_deferred.map((f) => ({
    key: `protection-${f.check}`,
    label: protectionLabel(f.check),
    status: "fail",
    detail: f.message,
    remedy: f.remedy,
    ...(f.remedy_url !== "" ? { linkUrl: f.remedy_url, linkLabel: "Open branch settings" } : {}),
  }));
  return [...applied, ...deferred];
}

// _check_credentials fails identically whether the transport secret or the
// reviewer secret is missing — credentialPresent (keyed on the transport
// record alone, same as step 1's chip) is what disambiguates "step 1 already
// owns this" from "this is a real, hoisted blocker" for this specific check.
function mintCredentialsRow(
  finding: EnginePreflightFinding | undefined,
  credentialPresent: boolean,
): ConnectStep3Row | null {
  if (finding?.status !== "fail" || !credentialPresent) return null;
  return {
    key: "mint_credentials",
    label: "Reviewer credential",
    status: "fail",
    detail: finding.message,
    remedy: `${finding.remedy} Add it in Settings (⚙) → Credentials.`,
  };
}

// Assembles the pinned status->chip table into the fixed presentation order:
// a hoisted blocker (if any) first, then reachability, scaffold, protection,
// reviewer access — no overall verdict row here, step 4 owns that.
export function buildConnectStep3Rows(
  readiness: OnboardingReadiness,
  credentialPresent: boolean,
): ConnectStep3Row[] {
  const { checklist, state } = readiness;
  const hoisted = mintCredentialsRow(findByCheck(checklist, "mint_credentials"), credentialPresent);
  const rows = [
    reachabilityRow(findByCheck(checklist, "push_repository")),
    workflowWriteRow(findByCheck(checklist, "workflow_write")),
    scaffoldRow(
      findByCheck(checklist, "review_and_merge_scaffold_pr"),
      state.scaffold_pr_number,
      state.scaffold_pr_url,
    ),
    ...protectionRows(readiness),
    collaboratorRow(findByCheck(checklist, "grant_reviewer_collaborator_access")),
  ];
  return hoisted !== null ? [hoisted, ...rows] : rows;
}
