import { useCallback, useEffect, useRef, useState } from "react";

import { api, type WorkflowTask } from "../../../api";
import type { TaggedEvent } from "../components/StreamPanel";
import { parseStreamPayload, parseTaskFromEvent } from "./workflow";
import {
  type WorkspaceNotice,
  noticeFromEvent,
  NOTICES_CAP,
} from "./workspaceNotices";

export interface LiveInvocationState {
  nodeUpdates: readonly WorkflowTask[];
  liveStreams: Readonly<Record<string, readonly TaggedEvent[]>>;
}

export interface LiveWorkspaceState {
  invocations: Readonly<Record<string, LiveInvocationState>>;
  streamError: string | null;
  notices: readonly WorkspaceNotice[];
  stickyNotice: WorkspaceNotice | null;
}

const EMPTY_INV: LiveInvocationState = { nodeUpdates: [], liveStreams: {} };

const STUCK_CLASS_EVENTS = new Set([
  "dag_stall",
  "router_escalate",
  "router_await_human",
  "injection_overflow",
  "frozen_chain_park",
]);

function applyNotice(
  state: LiveWorkspaceState,
  notice: WorkspaceNotice,
  isStuck: boolean,
): LiveWorkspaceState {
  const unbounded = [...state.notices, notice];
  const notices =
    unbounded.length > NOTICES_CAP
      ? unbounded.slice(unbounded.length - NOTICES_CAP)
      : unbounded;
  const stickyNotice = isStuck ? notice : state.stickyNotice;
  return { ...state, notices, stickyNotice };
}

function applyNodeEvent(
  state: LiveWorkspaceState,
  invId: string,
  task: WorkflowTask,
): LiveWorkspaceState {
  const prev = state.invocations[invId] ?? EMPTY_INV;
  const next: LiveInvocationState = {
    nodeUpdates: [...prev.nodeUpdates, task],
    liveStreams: prev.liveStreams,
  };
  return {
    ...state,
    invocations: { ...state.invocations, [invId]: next },
  };
}

function applyStreamEvent(
  state: LiveWorkspaceState,
  invId: string,
  role: string,
  tagged: TaggedEvent,
): LiveWorkspaceState {
  const prev = state.invocations[invId] ?? EMPTY_INV;
  const existing = prev.liveStreams[role] ?? [];
  const next: LiveInvocationState = {
    nodeUpdates: prev.nodeUpdates,
    liveStreams: { ...prev.liveStreams, [role]: [...existing, tagged] },
  };
  return {
    ...state,
    invocations: { ...state.invocations, [invId]: next },
  };
}

const NODE_EVENT_KINDS = new Set(["node_started", "node_completed"]);
const WORKSPACE_LIFECYCLE_KINDS = new Set([
  "workflow_created",
  "workspace_deleted",
  "workspace_archived",
  "workspace_unarchived",
  "workspace_paused",
  "workspace_resumed",
  "workspace_interrupted",
  "workspace_resuming",
  "job_started",
  "job_completed",
  "job_failed",
  "job_paused",
  "job_stranded",
  "job_retry_scheduled",
  "router_enqueue",
  "router_await_human",
  "router_escalate",
  "router_done",
  "injection_overflow",
  "frozen_chain_park",
  // Kept for onWorkspaceChanged test coverage; backend never emits this event.
  "manager_summary_ready",
  "dag_stall",
  "pr_merge_rederived",
  "deploy_verify_rederived",
  "verification_rederived",
  "slot_work_preserved",
  "slot_work_rescued",
  "slot_resume_waiting",
]);

export interface UseLiveWorkspaceOptions {
  onNewInvocation?: (invId: string) => void;
  onWorkspaceChanged?: () => void;
}

interface LiveRefs {
  liveSeq: { current: number };
  seenInvs: { current: Set<string> };
  noticeSeq: { current: number };
}

function handleLifecycleEvent(
  eventName: string,
  data: string,
  refs: LiveRefs,
  onWorkspaceChanged: (() => void) | undefined,
  setState: (updater: (prev: LiveWorkspaceState) => LiveWorkspaceState) => void,
): void {
  onWorkspaceChanged?.();
  const spec = noticeFromEvent(eventName, data);
  if (spec === null) return;
  refs.noticeSeq.current += 1;
  const notice: WorkspaceNotice = {
    id: `n-${refs.noticeSeq.current}`,
    timestamp: new Date().toISOString(),
    text: spec.text,
    severity: spec.severity,
  };
  setState((prev) => applyNotice(prev, notice, STUCK_CLASS_EVENTS.has(eventName)));
}

function handleNodeEvent(
  data: string,
  refs: LiveRefs,
  onNewInvocation: ((invId: string) => void) | undefined,
  setState: (updater: (prev: LiveWorkspaceState) => LiveWorkspaceState) => void,
): void {
  const parsed = parseTaskFromEvent(data);
  if (parsed === null) return;
  if (!refs.seenInvs.current.has(parsed.invId)) {
    refs.seenInvs.current.add(parsed.invId);
    onNewInvocation?.(parsed.invId);
  }
  setState((prev) => applyNodeEvent(prev, parsed.invId, parsed.task));
}

function handleStreamEvent(
  data: string,
  refs: LiveRefs,
  setState: (updater: (prev: LiveWorkspaceState) => LiveWorkspaceState) => void,
): void {
  const payload = parseStreamPayload(data);
  if (payload === null) return;
  refs.liveSeq.current += 1;
  const tagged: TaggedEvent = {
    id: `live-${payload.invId}-${payload.role}-${refs.liveSeq.current}`,
    event: payload.event,
  };
  setState((prev) =>
    applyStreamEvent(prev, payload.invId, payload.role, tagged),
  );
}

export function useLiveWorkspace(
  taskId: string | undefined,
  options: UseLiveWorkspaceOptions = {},
): LiveWorkspaceState {
  const [state, setState] = useState<LiveWorkspaceState>({
    invocations: {},
    streamError: null,
    notices: [],
    stickyNotice: null,
  });
  const liveSeq = useRef(0);
  const seenInvs = useRef<Set<string>>(new Set());
  const noticeSeq = useRef(0);
  const onNewInvocation = options.onNewInvocation;
  const onWorkspaceChanged = options.onWorkspaceChanged;

  const onEvent = useCallback(
    (event: { event: string; data: string }) => {
      const refs: LiveRefs = { liveSeq, seenInvs, noticeSeq };
      if (NODE_EVENT_KINDS.has(event.event)) {
        handleNodeEvent(event.data, refs, onNewInvocation, setState);
        return;
      }
      if (event.event === "stream") {
        handleStreamEvent(event.data, refs, setState);
        return;
      }
      if (WORKSPACE_LIFECYCLE_KINDS.has(event.event)) {
        handleLifecycleEvent(event.event, event.data, refs, onWorkspaceChanged, setState);
      }
    },
    [onNewInvocation, onWorkspaceChanged],
  );

  useEffect(() => {
    if (taskId === undefined) return undefined;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ invocations: {}, streamError: null, notices: [], stickyNotice: null });
    seenInvs.current = new Set();
    noticeSeq.current = 0;
    return api.subscribeWorkspace(taskId, {
      onEvent,
      onError: (detail) =>
        setState((prev) => ({ ...prev, streamError: detail })),
    });
  }, [taskId, onEvent]);

  return state;
}
