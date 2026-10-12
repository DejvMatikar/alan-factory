import type {
  Blocker,
  Handover,
  ManagerSummary,
  OpenQuestion,
  ParkReason,
  PendingDeployGate,
  TerminalOutcome,
  UnblockRecord,
  WorkspaceDetail,
} from "../../../api";
import { STATUS_GLYPHS, STATUS_LABELS, STATUS_STYLES } from "../../../components/StatusBadge";
import { formatActiveDuration } from "../../../lib/format";
import { stopResumeModeFor } from "./useWorkspaceLifecycle";
import type { LiveWorkspaceState } from "./workflowLive";
import type { WorkspaceNotice } from "./workspaceNotices";

export type SituationState =
  | "paused"
  | "completed"
  | "needs_you"
  | "escalated"
  | "failed"
  | "partial"
  | "deploy_wait"
  | "running"
  | "archived";

export type SituationPrimaryAction = "stop" | "resume" | "requeue" | null;

export interface SituationDecision {
  readonly question: OpenQuestion;
  readonly latest: Handover;
}

export interface Situation {
  readonly state: SituationState;
  readonly headline: string;
  readonly glyph: string | null;
  readonly toneClass: string;
  readonly body: string;
  readonly primaryAction: SituationPrimaryAction;
  readonly activeSeconds: number | null;
  readonly terminalOutcome: TerminalOutcome | null;
  readonly parkReason: ParkReason | null;
  readonly blockers: readonly Blocker[];
  readonly unblock: UnblockRecord | null;
  readonly decision: SituationDecision | null;
  readonly managerSummary: ManagerSummary | null;
  readonly deployGate: PendingDeployGate | null;
  readonly stickyNotice: WorkspaceNotice | null;
  readonly activityNotices: readonly WorkspaceNotice[];
}

const DIRECT_STATE_BY_STATUS: Partial<Record<WorkspaceDetail["status"], SituationState>> = {
  paused: "paused",
  completed: "completed",
  awaiting_input: "needs_you",
  escalated: "escalated",
  failed: "failed",
  partial: "partial",
  archived: "archived",
};

function stateOf(ws: WorkspaceDetail): SituationState {
  const direct = DIRECT_STATE_BY_STATUS[ws.status];
  if (direct !== undefined) return direct;
  return ws.pending_deploy_gate != null ? "deploy_wait" : "running";
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const HEADLINE_BY_STATE: Record<SituationState, string> = {
  paused: capitalize(STATUS_LABELS.paused),
  completed: capitalize(STATUS_LABELS.completed),
  needs_you: "Needs you",
  escalated: capitalize(STATUS_LABELS.escalated),
  failed: capitalize(STATUS_LABELS.failed),
  partial: capitalize(STATUS_LABELS.partial),
  deploy_wait: "Waiting on deploy verification",
  running: "Running",
  archived: capitalize(STATUS_LABELS.archived),
};

function runningBody(activeSeconds: number | null | undefined): string {
  if (activeSeconds == null) return "Working now.";
  return `Active for ${formatActiveDuration(activeSeconds)}.`;
}

export const DEPLOY_WAIT_COPY: Record<PendingDeployGate["path"], string> = {
  awaiting_restart: "confirming this merge has landed, or that the service already serves it",
  awaiting_live: "confirming the merge is live via local observation",
};

function deployWaitBody(gate: PendingDeployGate): string {
  return `Deploy verification is waiting on purpose: ${DEPLOY_WAIT_COPY[gate.path]}.`;
}

const BODY_BY_STATE: Record<SituationState, (ws: WorkspaceDetail) => string> = {
  running: (ws) => runningBody(ws.active_seconds),
  deploy_wait: (ws) => deployWaitBody(ws.pending_deploy_gate!),
  needs_you: () => "This workspace is waiting on you.",
  escalated: (ws) =>
    ws.terminal_outcome?.reason ?? "This workspace escalated and needs a decision.",
  failed: (ws) => ws.terminal_outcome?.reason ?? "This run failed.",
  partial: (ws) => ws.terminal_outcome?.reason ?? "This workspace shipped partially.",
  paused: () => "This workspace is paused.",
  completed: () => "This workspace delivered.",
  archived: () => "This workspace is archived.",
};

const STOP_RESUME_STATES: ReadonlySet<SituationState> = new Set([
  "running",
  "needs_you",
  "paused",
]);

function primaryActionFor(state: SituationState, ws: WorkspaceDetail): SituationPrimaryAction {
  return STOP_RESUME_STATES.has(state) ? stopResumeModeFor(ws) : null;
}

const DECISION_STATES: ReadonlySet<SituationState> = new Set(["needs_you", "escalated"]);

function blockingQuestionOf(handover: Handover): OpenQuestion | null {
  return handover.open_questions!.find((q) => q.blocking) ?? null;
}

function decisionFor(state: SituationState, ws: WorkspaceDetail): SituationDecision | null {
  if (!DECISION_STATES.has(state)) return null;
  const latest = ws.latest_handover;
  if (latest === null) return null;
  const question = blockingQuestionOf(latest);
  return question === null ? null : { question, latest };
}

const PARK_REASON_STATES: ReadonlySet<SituationState> = new Set(["needs_you", "escalated"]);
const BLOCKER_STATES: ReadonlySet<SituationState> = new Set([
  "needs_you",
  "escalated",
  "failed",
  "partial",
]);

function parkReasonFor(state: SituationState, ws: WorkspaceDetail): ParkReason | null {
  return PARK_REASON_STATES.has(state) ? (ws.park_reason ?? null) : null;
}

function blockersFor(state: SituationState, ws: WorkspaceDetail): readonly Blocker[] {
  return BLOCKER_STATES.has(state) ? (ws.blockers ?? []) : [];
}

export function situationOf(ws: WorkspaceDetail, live: LiveWorkspaceState): Situation {
  const state = stateOf(ws);
  return {
    state,
    headline: HEADLINE_BY_STATE[state],
    glyph: STATUS_GLYPHS[ws.status] ?? null,
    toneClass: STATUS_STYLES[ws.status],
    body: BODY_BY_STATE[state](ws),
    primaryAction: primaryActionFor(state, ws),
    activeSeconds: ws.active_seconds ?? null,
    terminalOutcome: ws.terminal_outcome ?? null,
    parkReason: parkReasonFor(state, ws),
    blockers: blockersFor(state, ws),
    unblock: ws.unblock ?? null,
    decision: decisionFor(state, ws),
    managerSummary: ws.manager_summary ?? null,
    deployGate: ws.pending_deploy_gate ?? null,
    stickyNotice: live.stickyNotice,
    activityNotices: live.notices,
  };
}
