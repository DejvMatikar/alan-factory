import { useMemo } from "react";

import type { DispatchInstructionsMap, Task, WorkflowTask } from "../../../api";
import Disclosure from "../../../ui/Disclosure";
import { deriveTaskTitle } from "../hooks/taskTitle";
import type { DocsState } from "../hooks/useInvocationDocs";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import { pickTasks, UsageLineSpan } from "./InvocationCard";
import RunsSection from "./RunsSection";
import { computeGroupUsage } from "../../../components/streamSummary";

const EMPTY_UPDATES: readonly WorkflowTask[] = Object.freeze([]);

function taskStatusLabel(status: Task["status"]): string {
  if (status === "completed") return "done";
  if (status === "awaiting_input") return "needs you";
  if (status === "failed") return "failed";
  return "in progress";
}

export interface TaskInvocationSectionProps {
  taskId: string;
  task: Task;
  wsTitle: string;
  invocationIds: readonly string[];
  live: LiveWorkspaceState;
  docsState: DocsState;
  readbacks: DispatchInstructionsMap;
  repo: string | null;
}

export default function TaskInvocationSection({
  taskId,
  task,
  wsTitle,
  invocationIds,
  live,
  docsState,
  readbacks,
  repo,
}: Readonly<TaskInvocationSectionProps>) {
  const usage = useMemo(() => {
    return computeGroupUsage(invocationIds, (invId) => {
      const doc = docsState.docs.get(invId) ?? null;
      const nodeUpdates = live.invocations[invId]?.nodeUpdates ?? EMPTY_UPDATES;
      return pickTasks(doc, nodeUpdates);
    });
  }, [invocationIds, docsState.docs, live.invocations]);

  const runCount = invocationIds.length;

  return (
    <Disclosure
      preset="group"
      defaultOpen
      testId={`task-section-${task.id}`}
      label={
        <>
          <span data-testid={`task-section-heading-${task.id}`}>
            {deriveTaskTitle(task, wsTitle)}
          </span>
          <span
            className="text-xs text-text-muted"
            data-testid={`task-section-status-${task.id}`}
          >
            {taskStatusLabel(task.status)}
          </span>
          <span
            className="text-xs text-text-muted"
            data-testid={`task-section-run-count-${task.id}`}
          >
            {runCount} run{runCount === 1 ? "" : "s"}
          </span>
          <UsageLineSpan
            testId={`task-section-cost-${task.id}`}
            inputTokens={usage.inputTokens}
            outputTokens={usage.outputTokens}
            totalCostUsd={usage.totalCostUsd}
          />
        </>
      }
    >
      <div className="mt-3">
        <RunsSection
          taskId={taskId}
          invocations={invocationIds}
          live={live}
          docsState={docsState}
          readbacks={readbacks}
          repo={repo}
        />
      </div>
    </Disclosure>
  );
}
