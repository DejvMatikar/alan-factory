const LABELS: Readonly<Record<string, string>> = {
  behind_plus_transient: "Branch Out-of-Date",
  stale_review: "Stale Review",
  stale_verifier_review: "Stale Verifier Review",
  transient_flake: "Known Flaky Check",
  sonar_hotspot: "SonarCloud Hotspot",
  sonar_no_annotations: "SonarCloud (No Findings)",
  sonar_genuine: "SonarCloud Quality Issue",
  dirty: "Merge Conflict",
  stale_measured_baseline: "Stale Measured Baseline",
  stale_measured_baseline_conflict: "Stale Baseline Update Conflict",
  still_computing: "Checks Still Running",
  blocked_pending: "Blocked (CI Pending)",
  blocked_no_signal: "Blocked (No Signal)",
  pre_existing_red: "Pre-Existing Failure",
  post_update_red: "Failed After Branch Update",
  no_check_results: "No Check Results Yet",
  closing_links_missing: "Closing Link Missing",
  genuine: "Genuine Blocker",
};

export const MERGE_BLOCK_CLASSES = Object.keys(LABELS) as readonly string[];

export function formatMergeBlockLabel(blockClass: string): string {
  return LABELS[blockClass] ?? blockClass;
}
