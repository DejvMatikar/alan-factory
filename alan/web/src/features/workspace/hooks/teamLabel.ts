const LABELS: Readonly<Record<string, string>> = {
  implementation_plan: "Implementation Plan",
  implementation: "Implementation",
  implementation_fix: "Implementation Fix",
  verification: "Verification",
  research: "Research",
  architecture: "Architecture",
  pr_merge: "PR Merge",
  decomposer: "Decomposer",
  brief_refine: "Brief Refinement",
  triage: "Triage",
};

export function formatTeamLabel(raw: string): string {
  const mapped = LABELS[raw];
  if (mapped !== undefined) return mapped;
  return raw
    .split(/[_-]/)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
