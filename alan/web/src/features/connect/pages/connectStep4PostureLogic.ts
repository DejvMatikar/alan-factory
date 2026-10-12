import type { EnginePosture, PreflightStatus, ProjectListEntry, WorkspaceSummary } from "../../../api";

export type PreflightRegister = "ready to dispatch" | "blocked" | "partially ready";

// The one place preflight's overall maps to step 4's readiness-chip copy
// (V3) — overall only ever holds pass/fail/unknown, and a check that has
// never run (null) reads as partially ready, never blocked. There is no
// fourth register.
export function preflightRegister(overall: PreflightStatus | null): PreflightRegister {
  if (overall === "pass") return "ready to dispatch";
  if (overall === "fail") return "blocked";
  return "partially ready";
}

// Client-side fold over the default workspace bucket only — listWorkspaces()
// never asks for the archived bucket, so a repo whose work has since been
// archived reads as having none (docs/architecture/connect_wizard.md states
// this as an accepted limitation). "Reached a PR number" — not "merged" —
// is all this predicate can actually claim: pr_number is set once a PR
// exists, not once it merges.
export function hasPrBearingWorkspace(
  workspaces: readonly WorkspaceSummary[],
  engineKey: string,
): boolean {
  return workspaces.some((w) => w.engine_key === engineKey && w.pr_number != null);
}

// Reuse precedence mirrors the already-ratified project-placement rule at
// home/fold.ts's projectSectionKey: a bound engine_key wins deterministically;
// an unbound project's intended_engine_key is consulted only when no bound
// match exists; a project bound to a DIFFERENT engine is never adopted just
// because it happens to carry this engine as its intent.
export function selectReusableProject(
  projects: readonly ProjectListEntry[],
  engineKey: string,
): ProjectListEntry | undefined {
  return (
    projects.find((p) => p.engine_key === engineKey) ??
    projects.find((p) => p.engine_key == null && p.intended_engine_key === engineKey)
  );
}

export interface PostureRung {
  value: EnginePosture;
  label: string;
  caption: string;
  selectable: boolean;
}

export const POSTURE_UNAVAILABLE_REASON =
  "Self-merge and autonomous stay unavailable until this repo has merged work for Alan to be judged by.";

const LABELS: Record<EnginePosture, string> = {
  observe: "Observe",
  propose: "Propose",
  self_merge: "Self-merge",
  autonomous: "Autonomous",
};

// Captions are written against admitted_teams(_TEAM_MIN_POSTURE) at
// alan/engine_config.py:62-88, not against the wireframe's phrasing:
// - observe admits no team at all (no team declares a minimum of observe).
// - propose admits research/brainstorming/architecture/planning/
//   implementation work, but no verify-or-merge team, so nothing this rung
//   dispatches can merge itself.
// - self_merge adds verification and pr_merge on top of propose's set.
// - autonomous adds the unattended maintenance teams (triage,
//   dependency_sweep, deploy_verify, maintenance) on top of self_merge's set.
const CAPTIONS: Record<EnginePosture, string> = {
  observe:
    "Alan reads and reports on this repo but dispatches no teams at all — no research, no planning, no changes.",
  propose:
    "Alan researches, plans, and implements changes for this repo, but every implementation pauses for your approval before it can merge.",
  self_merge: "Adds verifying and merging passing changes on its own, with no approval step.",
  autonomous:
    "Adds fully unattended maintenance — triage, dependency sweeps, and deploy checks — dispatched without you.",
};

const ALWAYS_SELECTABLE: ReadonlySet<EnginePosture> = new Set(["observe", "propose"]);

export function buildPostureRungs(mergedWork: boolean): PostureRung[] {
  return (Object.keys(LABELS) as EnginePosture[]).map((value) => ({
    value,
    label: LABELS[value],
    caption: CAPTIONS[value],
    selectable: ALWAYS_SELECTABLE.has(value) || mergedWork,
  }));
}

export function isSelectable(rows: readonly PostureRung[], value: EnginePosture): boolean {
  return rows.some((row) => row.value === value && row.selectable);
}

export type UnboundReason =
  | "preflight_blocked"
  | "preflight_unknown"
  | "bind_rejected"
  | "bind_conflict";
