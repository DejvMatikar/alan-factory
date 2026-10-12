import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import Disclosure from "../../../ui/Disclosure";
import ErrorBanner from "../../../ui/ErrorBanner";

import { errorMessageOf } from "../../../lib/errorMessage";
import {
  useRefreshWorkspaceDetail,
  useWorkspaceDetailQuery,
  useWorkspaceInvocationsQuery,
} from "../../../api/queries";
import {
  type DispatchInstructionsMap,
  type Task,
  type TaskStatus,
  type WorkspaceDetail,
} from "../../../api";
import Deliverables from "../components/Deliverables";
import InterTaskDAG from "../components/InterTaskDAG";
import RunsSection from "../components/RunsSection";
import { PLANNING_STATE_LEGEND } from "../hooks/planningState";
import TaskInvocationSection from "../components/TaskInvocationSection";
import TriageSweepCard from "../components/TriageSweepCard";
import { pickTasks, UsageLineSpan } from "../components/InvocationCard";
import MarkdownPanel from "../../../components/MarkdownPanel";
import { aggregateInvocationStatus, computeGroupUsage } from "../../../components/streamSummary";
import AcceptanceChecklist from "../components/AcceptanceChecklist";
import DeliverySummaryPanel from "../components/DeliverySummaryPanel";
import SituationCard from "../components/SituationCard";
import WorkspaceHeader from "../components/WorkspaceHeader";
import WorkspaceDetailsAside from "../components/WorkspaceDetailsAside";
import { useInstructionReadbacks } from "../hooks/useInstructionReadbacks";
import { useInvocationDocs } from "../hooks/useInvocationDocs";
import { topoSortTasks } from "../hooks/taskSort";
import { useWorkspaceLifecycle } from "../hooks/useWorkspaceLifecycle";
import { useLiveWorkspace } from "../hooks/workflowLive";

type StatusBucket = "done" | "needs-you" | "in-progress" | "failed";

function taskStatusBucket(status: TaskStatus): StatusBucket {
  if (status === "completed") return "done";
  if (status === "awaiting_input") return "needs-you";
  if (status === "failed") return "failed";
  return "in-progress";
}

function DecompositionSection({ ws }: Readonly<{ ws: WorkspaceDetail }>) {
  const rationale = ws.latest_handover?.split_rationale ?? null;
  if (rationale === null) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">Decomposition</h3>
      <p data-testid="split-rationale" className="text-xs text-text-muted">
        {rationale}
      </p>
    </section>
  );
}

export function workspaceStatusLine(ws: WorkspaceDetail): string {
  const tasks = ws.tasks!;
  if (tasks.length <= 1) return "";
  const buckets: Record<StatusBucket, number> = { done: 0, "needs-you": 0, "in-progress": 0, failed: 0 };
  for (const task of tasks) {
    buckets[taskStatusBucket(task.status)]++;
  }
  const total = tasks.length;
  const parts: string[] = [`${buckets.done} of ${total} done`];
  if (buckets["needs-you"] > 0) parts.push(`${buckets["needs-you"]} needs you`);
  if (buckets["in-progress"] > 0) parts.push(`${buckets["in-progress"]} in progress`);
  if (buckets.failed > 0) parts.push(`${buckets.failed} failed`);
  return parts.join(", ");
}

const NO_INVOCATIONS: readonly string[] = Object.freeze([]);

interface QueryFailure {
  readonly isError: boolean;
  readonly error: unknown;
}

function loadErrorOf(...failures: readonly QueryFailure[]): string | null {
  const failed = failures.find((failure) => failure.isError);
  return failed === undefined ? null : errorMessageOf(failed.error);
}

function useWorkspaceDetail(taskId: string | undefined) {
  const detail = useWorkspaceDetailQuery(taskId);
  const ws = detail.data ?? null;
  const invocationList = useWorkspaceInvocationsQuery(
    taskId,
    detail.epoch,
    ws !== null && ws.team !== "triage",
  );
  const refresh = useRefreshWorkspaceDetail(taskId);
  return {
    ws,
    invocations: invocationList.data ?? NO_INVOCATIONS,
    loadError: loadErrorOf(detail, invocationList),
    refresh,
  };
}

export default function Workspace() {
  const { taskId } = useParams<{ taskId: string }>();
  const { ws, invocations, loadError, refresh } = useWorkspaceDetail(taskId);

  const onNewInvocation = useCallback(() => refresh(), [refresh]);
  const onWorkspaceChanged = useCallback(() => refresh(), [refresh]);

  const live = useLiveWorkspace(ws?.team === "triage" ? undefined : taskId, {
    onNewInvocation,
    onWorkspaceChanged,
  });

  if (!taskId) {
    return (
      <p className="text-sm text-text-muted" data-testid="missing-task">
        No task id.
      </p>
    );
  }
  if (loadError) {
    return (
      <div role="alert" className="text-sm text-red-300">
        {loadError}
      </div>
    );
  }
  if (ws === null) {
    return (
      <p className="text-sm text-text-muted" data-testid="loading">
        Loading…
      </p>
    );
  }

  return (
    <WorkspaceBody
      taskId={taskId}
      ws={ws}
      invocations={invocations}
      live={live}
      onRefresh={refresh}
    />
  );
}

interface WorkspaceBodyProps {
  taskId: string;
  ws: WorkspaceDetail;
  invocations: readonly string[];
  live: ReturnType<typeof useLiveWorkspace>;
  onRefresh: () => void;
}

function PlanningStateLegend({ tasks }: Readonly<{ tasks: readonly Task[] }>) {
  if (!tasks.some(t => t.planning_state === "outline")) return null;
  return (
    <p className="text-xs text-text-muted" data-testid="planning-state-legend">
      {PLANNING_STATE_LEGEND}
    </p>
  );
}

interface GroupedInvocationsProps {
  taskId: string;
  ws: WorkspaceDetail;
  sortedTasks: readonly Task[];
  invsByTask: { byTask: Map<string, string[]>; unassigned: string[] };
  invocations: readonly string[];
  live: ReturnType<typeof useLiveWorkspace>;
  docsState: ReturnType<typeof useInvocationDocs>;
  readbacks: DispatchInstructionsMap;
}

function GroupedInvocations({
  taskId,
  ws,
  sortedTasks,
  invsByTask,
  invocations,
  live,
  docsState,
  readbacks,
}: Readonly<GroupedInvocationsProps>) {
  const unassignedUsage = useMemo(
    () =>
      computeGroupUsage(invsByTask.unassigned, (invId) =>
        pickTasks(docsState.docs.get(invId) ?? null, live.invocations[invId]?.nodeUpdates ?? []),
      ),
    [invsByTask.unassigned, docsState.docs, live.invocations],
  );
  const unassignedStatus = useMemo(
    () =>
      aggregateInvocationStatus(
        invsByTask.unassigned.map((invId) => docsState.docs.get(invId)?.status),
      ),
    [invsByTask.unassigned, docsState.docs],
  );

  if (ws.tasks!.length <= 1) {
    return (
      <RunsSection
        key={taskId}
        taskId={taskId}
        invocations={invocations}
        live={live}
        docsState={docsState}
        readbacks={readbacks}
        repo={ws.engine_key ?? null}
      />
    );
  }
  const unassignedCount = invsByTask.unassigned.length;
  return (
    <div className="space-y-6" data-testid="grouped-invocations">
      {unassignedCount > 0 && (
        <Disclosure
          preset="group"
          defaultOpen
          testId="task-section-ungrouped"
          label={
            <>
              <span>{"Plan & decompose"}</span>
              <span
                className="text-xs text-text-muted"
                data-testid="task-section-status-ungrouped"
              >
                {unassignedStatus}
              </span>
              <span className="text-xs text-text-muted">
                {unassignedCount} run{unassignedCount === 1 ? "" : "s"}
              </span>
              <UsageLineSpan
                testId="task-section-cost-ungrouped"
                inputTokens={unassignedUsage.inputTokens}
                outputTokens={unassignedUsage.outputTokens}
                totalCostUsd={unassignedUsage.totalCostUsd}
              />
            </>
          }
        >
          <div className="mt-3">
            <RunsSection
              key={taskId}
              taskId={taskId}
              invocations={invsByTask.unassigned}
              live={live}
              docsState={docsState}
              readbacks={readbacks}
              repo={ws.engine_key ?? null}
            />
          </div>
        </Disclosure>
      )}
      {sortedTasks.map((task) => (
        <TaskInvocationSection
          key={task.id}
          taskId={taskId}
          task={task}
          wsTitle={ws.title}
          invocationIds={invsByTask.byTask.get(task.id) ?? []}
          live={live}
          docsState={docsState}
          readbacks={readbacks}
          repo={ws.engine_key ?? null}
        />
      ))}
    </div>
  );
}

function unprovenClauseIdSet(ws: WorkspaceDetail): ReadonlySet<string> {
  return new Set(ws.unproven_clause_ids);
}

interface WorkspaceSituationSlotProps {
  readonly ws: WorkspaceDetail;
  readonly taskId: string;
  readonly live: ReturnType<typeof useLiveWorkspace>;
  readonly lifecycle: ReturnType<typeof useWorkspaceLifecycle>;
  readonly onRefresh: () => void;
}

function WorkspaceSituationSlot({
  ws,
  taskId,
  live,
  lifecycle,
  onRefresh,
}: Readonly<WorkspaceSituationSlotProps>) {
  return (
    <div data-testid="workspace-situation-slot">
      <SituationCard
        ws={ws}
        taskId={taskId}
        live={live}
        lifecycle={lifecycle}
        onRefresh={onRefresh}
      />
    </div>
  );
}

interface WorkspaceMainSlotProps {
  readonly taskId: string;
  readonly ws: WorkspaceDetail;
  readonly tasks: readonly Task[];
  readonly sortedTasks: readonly Task[];
  readonly invsByTask: { byTask: Map<string, string[]>; unassigned: string[] };
  readonly invocations: readonly string[];
  readonly live: ReturnType<typeof useLiveWorkspace>;
  readonly docsState: ReturnType<typeof useInvocationDocs>;
  readonly readbacks: DispatchInstructionsMap;
}

function WorkspaceMainSlot({
  taskId,
  ws,
  tasks,
  sortedTasks,
  invsByTask,
  invocations,
  live,
  docsState,
  readbacks,
}: Readonly<WorkspaceMainSlotProps>) {
  return (
    <div className="space-y-6" data-testid="workspace-main-slot">
      {ws.description && (
        <MarkdownPanel
          title="Task description"
          ariaLabel="Task description"
          testId="workspace-task-description"
          defaultOpen={false}
          contentClassName="mt-1 text-sm text-text-primary"
        >
          {ws.description}
        </MarkdownPanel>
      )}
      {tasks.length > 1 && (
        <p data-testid="workspace-status-line" className="text-sm text-text-muted">
          {workspaceStatusLine(ws)}
        </p>
      )}
      {tasks.length > 1 && (
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Task graph</h3>
          <div className="overflow-x-auto">
            <InterTaskDAG
              nodes={tasks.map(t => ({ id: t.id, label: t.title ?? t.team ?? t.id, planningState: t.planning_state, status: t.status }))}
              edges={tasks.flatMap(t => t.depends_on!.map(dep => ({ from: dep, to: t.id })))}
            />
          </div>
          <PlanningStateLegend tasks={tasks} />
        </section>
      )}
      <AcceptanceChecklist
        requirements={ws.requirements}
        assumptions={ws.assumptions}
        tasks={tasks}
        unprovenClauseIds={unprovenClauseIdSet(ws)}
        workspaceStatus={ws.status}
      />
      <DecompositionSection ws={ws} />
      <Deliverables ws={ws} taskId={taskId} />
      <section className="space-y-2">
        <h3 className="text-sm font-medium">Invocations</h3>
        <GroupedInvocations
          taskId={taskId}
          ws={ws}
          sortedTasks={sortedTasks}
          invsByTask={invsByTask}
          invocations={invocations}
          live={live}
          docsState={docsState}
          readbacks={readbacks}
        />
      </section>
      <DeliverySummaryPanel taskId={taskId} status={ws.status} liveAt={ws.live_at} />
    </div>
  );
}

function WorkspaceBody({
  taskId,
  ws,
  invocations,
  live,
  onRefresh,
}: Readonly<WorkspaceBodyProps>) {
  const docsState = useInvocationDocs(taskId, invocations);
  const { readbacks } = useInstructionReadbacks(taskId);
  const lifecycle = useWorkspaceLifecycle(taskId, ws, onRefresh);

  const stickyNoticeRef = useRef<HTMLOutputElement>(null);
  const [stickyCleared, setStickyCleared] = useState(false);
  const handleStickyCleared = useCallback(() => {
    setStickyCleared(true);
  }, []);
  useEffect(() => {
    if (stickyCleared) stickyNoticeRef.current?.focus();
  }, [stickyCleared]);

  const tasks = ws.tasks!;
  const sortedTasks = useMemo(() => topoSortTasks(tasks), [tasks]);

  const invsByTask = useMemo(() => {
    const taskIdSet = new Set(tasks.map((t) => t.id));
    const byTask = new Map<string, string[]>();
    const unassigned: string[] = [];
    for (const invId of invocations) {
      const sourceTaskId = docsState.docs.get(invId)?.sourceTaskId ?? null;
      if (sourceTaskId !== null && taskIdSet.has(sourceTaskId)) {
        const arr = byTask.get(sourceTaskId) ?? [];
        arr.push(invId);
        byTask.set(sourceTaskId, arr);
      } else {
        unassigned.push(invId);
      }
    }
    return { byTask, unassigned };
  }, [invocations, tasks, docsState.docs]);

  const pageBanners = (
    <>
      {live.streamError !== null && (
        <div
          data-testid="stream-warning"
          className="rounded-md bg-amber-500/15 px-3 py-2 text-xs text-amber-300"
        >
          Live updates unavailable: {live.streamError}
        </div>
      )}
      {lifecycle.toggleError !== null && (
        <ErrorBanner data-testid="archive-error">{lifecycle.toggleError}</ErrorBanner>
      )}
      {lifecycle.stopResumeError !== null && (
        <ErrorBanner data-testid="stop-resume-error">{lifecycle.stopResumeError}</ErrorBanner>
      )}
    </>
  );

  if (ws.team === "triage") {
    return (
      <div className="space-y-8">
        <WorkspaceHeader ws={ws} taskId={taskId} lifecycle={lifecycle} />
        {pageBanners}
        <TriageSweepCard />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <WorkspaceHeader ws={ws} taskId={taskId} lifecycle={lifecycle} />
      {pageBanners}
      <div
        className="grid grid-cols-1 gap-8 sm:grid-cols-[minmax(0,1fr)_22rem]"
        data-testid="workspace-columns"
      >
        <div className="order-1 space-y-4 sm:order-2">
          <WorkspaceDetailsAside
            ws={ws}
            taskId={taskId}
            onInstructionCleared={handleStickyCleared}
          />
          {stickyCleared && (
            <output
              ref={stickyNoticeRef}
              tabIndex={-1}
              data-testid="sticky-instruction-cleared-notice"
              className="text-xs text-text-muted"
            >
              Persisted instruction cleared.
            </output>
          )}
        </div>
        <div className="order-2 space-y-8 sm:order-1">
          <WorkspaceSituationSlot
            ws={ws}
            taskId={taskId}
            live={live}
            lifecycle={lifecycle}
            onRefresh={onRefresh}
          />
          <WorkspaceMainSlot
            taskId={taskId}
            ws={ws}
            tasks={tasks}
            sortedTasks={sortedTasks}
            invsByTask={invsByTask}
            invocations={invocations}
            live={live}
            docsState={docsState}
            readbacks={readbacks}
          />
        </div>
      </div>
    </div>
  );
}
