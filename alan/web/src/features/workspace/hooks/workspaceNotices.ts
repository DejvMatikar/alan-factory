import { formatMergeBlockLabel } from "../../../mergeBlockLabel";
import { parseObject } from "./workflow";

export type Severity = "info" | "warn" | "error";

export interface WorkspaceNotice {
  id: string;
  timestamp: string;
  text: string;
  severity: Severity;
}

export const NOTICES_CAP = 50;

interface NoticeSpec {
  text: string;
  severity: Severity;
}

const SILENT_EVENTS = new Set(["router_enqueue", "router_done"]);

const EVENT_SPECS: Record<string, NoticeSpec> = {
  workflow_created: { text: "Workflow started", severity: "info" },
  workspace_deleted: { text: "Workspace deleted", severity: "error" },
  workspace_archived: { text: "Workspace archived", severity: "info" },
  workspace_unarchived: { text: "Workspace unarchived", severity: "info" },
  workspace_paused: { text: "Workspace paused", severity: "warn" },
  workspace_resumed: { text: "Workspace resumed", severity: "info" },
  workspace_interrupted: { text: "Workspace interrupted", severity: "warn" },
  workspace_resuming: { text: "Workspace resuming", severity: "info" },
  job_started: { text: "Job started", severity: "info" },
  job_completed: { text: "Job completed", severity: "info" },
  job_failed: { text: "Job failed", severity: "error" },
  job_paused: { text: "Job paused", severity: "warn" },
  job_stranded: {
    text: "Job could not be requeued — needs you",
    severity: "error",
  },
  job_retry_scheduled: { text: "Job retrying after failure", severity: "warn" },
  pr_merge_rederived: { text: "PR merge restarted", severity: "info" },
  deploy_verify_rederived: { text: "Deploy verify restarted", severity: "info" },
  verification_rederived: { text: "Verification restarted", severity: "info" },
};

type DynamicHandler = (parsed: Record<string, unknown>) => NoticeSpec;

function routerReason(parsed: Record<string, unknown>, fallback: string): string {
  return typeof parsed.reason === "string" && parsed.reason
    ? parsed.reason
    : fallback;
}

function routerNext(parsed: Record<string, unknown>): string {
  return typeof parsed.next_team === "string" ? ` → ${parsed.next_team}` : "";
}

function routerBlockClass(parsed: Record<string, unknown>): string {
  return typeof parsed.block_class === "string" && parsed.block_class
    ? ` [${formatMergeBlockLabel(parsed.block_class)}]`
    : "";
}

function escalateNotice(parsed: Record<string, unknown>): NoticeSpec {
  const text = `Escalated — ${routerReason(parsed, "manual intervention required")}${routerBlockClass(parsed)}${routerNext(parsed)}`;
  return { text, severity: "error" };
}

function awaitHumanNotice(parsed: Record<string, unknown>): NoticeSpec {
  const text = `Waiting for your input — ${routerReason(parsed, "input required")}${routerBlockClass(parsed)}${routerNext(parsed)}`;
  return { text, severity: "warn" };
}

function injectionOverflowNotice(parsed: Record<string, unknown>): NoticeSpec {
  let count: number;
  if (typeof parsed.dropped_count === "number") {
    count = parsed.dropped_count;
  } else if (typeof parsed.dropped_count === "string") {
    count = Number(parsed.dropped_count) || 0;
  } else {
    count = 0;
  }
  return { text: `${count} injected task(s) dropped`, severity: "warn" };
}

function dagStallNotice(parsed: Record<string, unknown>): NoticeSpec {
  const reason =
    typeof parsed.reason === "string" && parsed.reason
      ? parsed.reason
      : "unknown reason";
  return { text: `Workflow stalled: ${reason}`, severity: "warn" };
}

function frozenChainNotice(parsed: Record<string, unknown>): NoticeSpec {
  const reason =
    typeof parsed.reason === "string" && parsed.reason
      ? parsed.reason
      : "unknown reason";
  return { text: `Frozen dependency chain: ${reason}`, severity: "warn" };
}

function slotWorkPreservedNotice(parsed: Record<string, unknown>): NoticeSpec {
  const n = typeof parsed.n_commits === "string" ? parsed.n_commits : "?";
  return { text: `${n} commit(s) preserved from interrupted run`, severity: "info" };
}

function slotWorkRescuedNotice(parsed: Record<string, unknown>): NoticeSpec {
  const ref =
    typeof parsed.rescue_ref === "string" ? parsed.rescue_ref : "rescue ref";
  return { text: `Prior commits rescued to ${ref}; restarting clean`, severity: "warn" };
}

function slotResumeWaitingNotice(parsed: Record<string, unknown>): NoticeSpec {
  const idx =
    typeof parsed.slot_index === "string" ? parsed.slot_index : "?";
  return {
    text: `Resumed session waiting for its pinned slot (${idx}) to free up`,
    severity: "warn",
  };
}

function prNumberSuffix(parsed: Record<string, unknown>): string {
  let pr: number | null = null;
  if (typeof parsed.pr_number === "number") {
    pr = parsed.pr_number;
  } else if (typeof parsed.pr_number === "string") {
    pr = Number(parsed.pr_number) || null;
  }
  return pr === null ? "" : ` PR #${pr}`;
}

function cannotVerifyNotice(parsed: Record<string, unknown>): NoticeSpec {
  return {
    text: `Verification tooling failure — cannot access${prNumberSuffix(parsed)} code, needs human attention`,
    severity: "warn",
  };
}

function reviewPostFailedNotice(parsed: Record<string, unknown>): NoticeSpec {
  return {
    text: `Verifier review POST failed${prNumberSuffix(parsed)} — check ALAN_VERIFIER_GH_TOKEN, needs human attention`,
    severity: "error",
  };
}

const DYNAMIC_HANDLERS: Record<string, DynamicHandler> = {
  router_escalate: escalateNotice,
  router_await_human: awaitHumanNotice,
  injection_overflow: injectionOverflowNotice,
  dag_stall: dagStallNotice,
  frozen_chain_park: frozenChainNotice,
  cannot_verify: cannotVerifyNotice,
  slot_work_preserved: slotWorkPreservedNotice,
  slot_work_rescued: slotWorkRescuedNotice,
  slot_resume_waiting: slotResumeWaitingNotice,
  review_post_failed: reviewPostFailedNotice,
};

function humanize(eventName: string): string {
  const words = eventName.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function noticeFromEvent(
  eventName: string,
  data: string,
): NoticeSpec | null {
  if (SILENT_EVENTS.has(eventName)) return null;
  if (data === "") return null;
  const parsed = parseObject(data);
  if (parsed === null) return null;
  const dynamic = DYNAMIC_HANDLERS[eventName];
  if (dynamic !== undefined) return dynamic(parsed);
  const spec = EVENT_SPECS[eventName];
  if (spec !== undefined) return spec;
  return { text: humanize(eventName), severity: "info" };
}
