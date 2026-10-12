import { useMemo } from "react";

import type { DispatchInstructionsReadback, Handover, WorkflowTask } from "../../../api";
import Button from "../../../components/Button";
import NodeStatusBadge from "../../../components/NodeStatusBadge";
import { formatUsageRow, sumTaskUsage } from "../../../components/streamSummary";
import { formatCostUsd, formatDate } from "../../../lib/format";
import Disclosure from "../../../ui/Disclosure";
import { formatTeamLabel } from "../hooks/teamLabel";
import { resolveHandoverTargetLabel } from "../hooks/useHandovers";
import type { LiveInvocationState } from "../hooks/workflowLive";
import HandoverDetailPanel from "./HandoverDetailPanel";
import InstructionBadge from "./InstructionBadge";
import { pickTasks, renderInvocationDuration, statusForBadge, type DocState } from "./InvocationCard";
import { computeDuration } from "./WorkflowDAG";

export interface RunRowProps {
  taskId: string;
  invId: string;
  doc: DocState | null;
  loadError: string | null;
  liveInvocation: LiveInvocationState;
  readback: DispatchInstructionsReadback | undefined;
  handover: Handover | null;
  repo: string | null;
}

function runDescription(doc: DocState | null, loadError: string | null): string {
  if (loadError !== null) return "load failed";
  if (doc === null) return "loading…";
  return formatTeamLabel(doc.team);
}

function RunStepRow({ task }: Readonly<{ task: WorkflowTask }>) {
  const duration = computeDuration(task);
  const usage = formatUsageRow(task.input_tokens, task.output_tokens, task.total_cost_usd);
  return (
    <li
      data-testid={`run-row-step-${task.id}`}
      className="flex flex-wrap items-center gap-2 rounded border border-border bg-surface px-2 py-1 text-xs"
    >
      <span className="font-medium">{task.role}</span>
      <NodeStatusBadge status={task.status} />
      {duration !== null && <span className="tabular-nums text-text-muted">{duration}</span>}
      {usage !== null && <span className="tabular-nums text-text-muted">{usage}</span>}
    </li>
  );
}

interface RunRowFactsProps {
  invId: string;
  doc: DocState | null;
  loadError: string | null;
  readback: DispatchInstructionsReadback | undefined;
  duration: string | null;
  totalCostUsd: number | null;
  handoffLabel: string | null;
}

function RunRowFacts({
  invId,
  doc,
  loadError,
  readback,
  duration,
  totalCostUsd,
  handoffLabel,
}: Readonly<RunRowFactsProps>) {
  return (
    <>
      <span className="font-medium" data-testid={`run-row-description-${invId}`}>
        {runDescription(doc, loadError)}
      </span>
      {doc !== null && <NodeStatusBadge status={statusForBadge(doc.status)} />}
      {readback !== undefined && (
        <InstructionBadge invId={invId} readback={readback} showDetails={false} />
      )}
      {doc !== null && (
        <span
          className="tabular-nums text-xs text-text-muted"
          data-testid={`run-row-started-${invId}`}
        >
          {formatDate(doc.startedAt)}
        </span>
      )}
      {duration !== null && (
        <span
          className="tabular-nums text-xs text-text-muted"
          data-testid={`run-row-duration-${invId}`}
        >
          {duration}
        </span>
      )}
      {totalCostUsd !== null && (
        <span
          className="tabular-nums text-xs text-text-muted"
          data-testid={`run-row-cost-${invId}`}
        >
          {formatCostUsd(totalCostUsd)}
        </span>
      )}
      {handoffLabel !== null && (
        <span className="text-xs text-text-muted" data-testid={`run-row-handoff-${invId}`}>
          → {handoffLabel}
        </span>
      )}
    </>
  );
}

export default function RunRow({
  taskId,
  invId,
  doc,
  loadError,
  liveInvocation,
  readback,
  handover,
  repo,
}: Readonly<RunRowProps>) {
  const tasks = useMemo(
    () => pickTasks(doc, liveInvocation.nodeUpdates),
    [doc, liveInvocation.nodeUpdates],
  );
  const usage = useMemo(() => sumTaskUsage(tasks), [tasks]);
  const duration = doc !== null ? renderInvocationDuration(doc) : null;
  const handoffLabel = handover !== null ? resolveHandoverTargetLabel(handover) : null;
  const fullViewUrl = `/workspaces/${encodeURIComponent(taskId)}/invocations/${encodeURIComponent(invId)}`;

  return (
    <li className="flex items-start gap-2" data-testid={`run-row-${invId}`}>
      <Disclosure
        className="min-w-0 flex-1"
        preset="group"
        defaultOpen={false}
        testId={`run-row-disclosure-${invId}`}
        summaryTestId={`run-row-summary-${invId}`}
        label={
          <RunRowFacts
            invId={invId}
            doc={doc}
            loadError={loadError}
            readback={readback}
            duration={duration}
            totalCostUsd={usage.totalCostUsd}
            handoffLabel={handoffLabel}
          />
        }
      >
        <div className="mt-2 space-y-3">
          {loadError !== null && (
            <p className="text-xs text-red-300" data-testid={`run-row-error-${invId}`}>
              {loadError}
            </p>
          )}
          {tasks.length > 0 && (
            <ul className="space-y-1" data-testid={`run-row-steps-${invId}`}>
              {tasks.map((task) => (
                <RunStepRow key={task.id} task={task} />
              ))}
            </ul>
          )}
          {handover !== null && (
            <div data-testid={`run-row-handover-${invId}`}>
              <HandoverDetailPanel handover={handover} repo={repo} />
            </div>
          )}
        </div>
      </Disclosure>
      <Button
        variant="ghost"
        density="compact"
        linkTo={fullViewUrl}
        aria-label={`Open full view of run ${invId}`}
        data-testid={`run-row-full-view-${invId}`}
        className="shrink-0"
      >
        Open full view
      </Button>
    </li>
  );
}
