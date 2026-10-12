import { useCallback, useState } from "react";

import { api, type DispatchInstructionsReadback, type NodeDetailTab, type WorkflowDoc, type WorkflowTask } from "../../../api";
import { useDocQuery } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";
import { mergeNodeUpdate, resolveStepLabel } from "../hooks/workflow";
import { useAutoScroll } from "../../../hooks/useAutoScroll";
import { formatTeamLabel } from "../hooks/teamLabel";
import DescribedIconButton from "../../../components/DescribedIconButton";
import NodeStatusBadge from "../../../components/NodeStatusBadge";
import InstructionBadge from "./InstructionBadge";
import NodeDetailModal from "./NodeDetailModal";
import StreamPanel, { type TaggedEvent } from "./StreamPanel";
import { formatUsageRow, sumTaskUsage } from "../../../components/streamSummary";
import WorkflowDAG, { computeDuration } from "./WorkflowDAG";

export interface InvocationCardProps {
  taskId: string;
  invId: string;
  liveStreams: Readonly<Record<string, readonly TaggedEvent[]>>;
  nodeUpdates: readonly WorkflowTask[];
  defaultExpanded?: boolean;
  instructionReadback?: DispatchInstructionsReadback | undefined;
}

export interface DocState {
  team: string;
  status: WorkflowDoc["status"];
  startedAt: string;
  completedAt: string | null;
  sourceTaskId: string | null;
  tasks: WorkflowTask[];
}

interface DocLoadResult {
  doc: DocState | null;
  error: string | null;
}

function applyNodeUpdates(
  base: WorkflowTask[],
  updates: readonly WorkflowTask[],
): WorkflowTask[] {
  return updates.reduce<WorkflowTask[]>(
    (acc, update) => mergeNodeUpdate(acc, update),
    base,
  );
}

async function fetchInvocationDoc(taskId: string, invId: string): Promise<DocState> {
  const d = await api.getInvocation(taskId, invId);
  return {
    team: d.team,
    status: d.status,
    startedAt: d.started_at,
    completedAt: d.completed_at ?? null,
    sourceTaskId: d.source_task_id ?? null,
    tasks: d.tasks!,
  };
}

export function useInvocationDoc(taskId: string, invId: string): DocLoadResult {
  const view = useDocQuery("invocation-card", `${taskId}|${invId}`, () =>
    fetchInvocationDoc(taskId, invId),
  );
  return {
    doc: view.data ?? null,
    error: view.isError ? errorMessageOf(view.error) : null,
  };
}

function statusAttr(doc: DocState | null): string {
  return doc === null ? "loading" : doc.status;
}

export function pickTasks(
  doc: DocState | null,
  nodeUpdates: readonly WorkflowTask[],
): WorkflowTask[] {
  if (doc === null) return [];
  return applyNodeUpdates(doc.tasks, nodeUpdates);
}

interface ModalState {
  nodeId: string;
  tab: NodeDetailTab;
}

export default function InvocationCard({
  taskId,
  invId,
  liveStreams,
  nodeUpdates,
  defaultExpanded = true,
  instructionReadback,
}: Readonly<InvocationCardProps>) {
  const { doc, error } = useInvocationDoc(taskId, invId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [modal, setModal] = useState<ModalState | null>(null);

  const tasks = pickTasks(doc, nodeUpdates);
  const selected = tasks.find((t) => t.id === selectedId) ?? null;
  const handleSelect = useCallback((id: string) => setSelectedId(id), []);
  const toggle = useCallback(() => setExpanded((prev) => !prev), []);
  const handleOpenModal = useCallback(
    (nodeId: string, tab: NodeDetailTab) => setModal({ nodeId, tab }),
    [],
  );
  const handleCloseModal = useCallback(() => setModal(null), []);
  const showBody = expanded && doc !== null;

  return (
    <article
      className="space-y-3 rounded-lg border border-border bg-surface-elevated p-4"
      data-testid={`invocation-card-${invId}`}
      data-status={statusAttr(doc)}
    >
      <CardHeader
        invId={invId}
        doc={doc}
        tasks={tasks}
        expanded={expanded}
        onToggle={toggle}
        instructionReadback={instructionReadback}
      />
      {error !== null && (
        <p
          className="text-xs text-red-300"
          data-testid={`invocation-error-${invId}`}
        >
          {error}
        </p>
      )}
      {showBody && (
        <CardBody
          taskId={taskId}
          invId={invId}
          tasks={tasks}
          selected={selected}
          selectedId={selectedId}
          onSelect={handleSelect}
          onOpenModal={handleOpenModal}
          liveStreams={liveStreams}
        />
      )}
      {modal !== null && (
        <NodeDetailModal
          taskId={taskId}
          invId={invId}
          nodeId={modal.nodeId}
          stepLabel={resolveStepLabel(tasks, modal.nodeId)}
          initialTab={modal.tab}
          onClose={handleCloseModal}
        />
      )}
    </article>
  );
}

interface CardHeaderProps {
  invId: string;
  doc: DocState | null;
  tasks: readonly WorkflowTask[];
  expanded: boolean;
  onToggle: () => void;
  instructionReadback: DispatchInstructionsReadback | undefined;
}

function ToggleButton({
  invId,
  expanded,
  onToggle,
}: Readonly<{ invId: string; expanded: boolean; onToggle: () => void }>) {
  const label = `${expanded ? "Collapse" : "Expand"} invocation ${invId}`;
  return (
    <DescribedIconButton
      glyph={expanded ? "▾" : "▸"}
      label={label}
      description={expanded ? "Hide this run's steps" : "Show this run's steps"}
      testId={`invocation-toggle-${invId}`}
      expanded={expanded}
      onClick={onToggle}
    />
  );
}

interface UsageLineSpanProps {
  testId: string;
  inputTokens: number | null;
  outputTokens: number | null;
  totalCostUsd: number | null;
}

export function UsageLineSpan({
  testId,
  inputTokens,
  outputTokens,
  totalCostUsd,
}: Readonly<UsageLineSpanProps>) {
  const row = formatUsageRow(inputTokens, outputTokens, totalCostUsd);
  if (row === null) return null;
  return (
    <span className="text-[10px] text-text-muted" data-testid={testId}>
      {row}
    </span>
  );
}

function StepCount({ count }: Readonly<{ count: number }>) {
  return (
    <span className="text-xs text-text-muted">
      {count} step{count === 1 ? "" : "s"}
    </span>
  );
}

function CardHeader({
  invId,
  doc,
  tasks,
  expanded,
  onToggle,
  instructionReadback,
}: Readonly<CardHeaderProps>) {
  const duration = doc ? renderInvocationDuration(doc) : null;
  const usage = sumTaskUsage(tasks);
  return (
    <header className="flex items-center justify-between">
      <div className="flex items-center gap-3">
        <ToggleButton invId={invId} expanded={expanded} onToggle={onToggle} />
        <h3 className="text-sm font-medium">
          {doc ? formatTeamLabel(doc.team) : "loading…"}{" "}
          <span className="font-mono text-xs text-text-muted">{invId}</span>
        </h3>
        {doc !== null && (
          <NodeStatusBadge status={statusForBadge(doc.status)} />
        )}
        {instructionReadback !== undefined && (
          <InstructionBadge invId={invId} readback={instructionReadback} />
        )}
        {duration !== null && (
          <span className="text-xs text-text-muted">{duration}</span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {doc !== null && <StepCount count={tasks.length} />}
        <UsageLineSpan
          testId={`invocation-card-${invId}-usage`}
          inputTokens={usage.inputTokens}
          outputTokens={usage.outputTokens}
          totalCostUsd={usage.totalCostUsd}
        />
      </div>
    </header>
  );
}

interface CardBodyProps {
  taskId: string;
  invId: string;
  tasks: readonly WorkflowTask[];
  selected: WorkflowTask | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpenModal: (nodeId: string, tab: NodeDetailTab) => void;
  liveStreams: Readonly<Record<string, readonly TaggedEvent[]>>;
}

export function CardBody({
  taskId,
  invId,
  tasks,
  selected,
  selectedId,
  onSelect,
  onOpenModal,
  liveStreams,
}: Readonly<CardBodyProps>) {
  const panelRef = useAutoScroll([selectedId], "nearest");
  return (
    <div className="space-y-3">
      <WorkflowDAG
        tasks={tasks}
        selectedId={selectedId}
        onSelect={onSelect}
        onOpenModal={onOpenModal}
        testId={`workflow-graph-${invId}`}
      />
      {selected !== null && (
        <div ref={panelRef}>
          <StreamPanel
            key={`${taskId}:${invId}:${selected.role}`}
            taskId={taskId}
            invId={invId}
            role={selected.role}
            liveEvents={liveStreams[selected.role] ?? []}
            nodeError={selected.status === "failed" ? selected.error : null}
            nodeStatus={selected.status}
          />
        </div>
      )}
    </div>
  );
}

export function statusForBadge(
  status: WorkflowDoc["status"],
): "pending" | "running" | "success" | "failed" {
  if (status === "escalated" || status === "interrupted") return "failed";
  return status;
}

export function renderInvocationDuration(doc: DocState): string | null {
  if (doc.status === "pending") return null;
  const fakeTask: WorkflowTask = {
    id: "_",
    type: "io.alan.LLMStep",
    role: "_",
    model: "_",
    depends_on: [],
    status: "running",
    started_at: doc.startedAt,
    completed_at: doc.completedAt,
    stream_path: null,
    dispatched: [],
    error: null,
    input_tokens: null,
    output_tokens: null,
    cache_creation_input_tokens: null,
    cache_read_input_tokens: null,
    total_cost_usd: null,
    usage_raw: null,
  };
  return computeDuration(fakeTask);
}
