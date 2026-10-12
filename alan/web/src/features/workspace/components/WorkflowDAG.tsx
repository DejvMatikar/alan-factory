import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useId, useMemo } from "react";

import type { NodeDetailTab, WorkflowTask } from "../../../api";
import { NODE_WIDTH, ROW_HEIGHT, computeLayout, stepDisplayName } from "../hooks/workflow";
import NodeStatusBadge from "../../../components/NodeStatusBadge";
import { StepCardIcons } from "./NodeCardIcons";
import { formatUsageRow } from "../../../components/streamSummary";
import { SELECTION_FOCUS_CLASS, ariaCurrentSelection } from "../../../shell/selection";

export function computeDuration(task: WorkflowTask): string | null {
  if (task.started_at == null) return null;
  const start = Date.parse(task.started_at);
  const end = task.completed_at ? Date.parse(task.completed_at) : null;
  if (Number.isNaN(start)) return null;
  if (end === null || Number.isNaN(end)) return "running…";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  return `${seconds}s`;
}

export interface StepNodeCardProps {
  task: WorkflowTask;
  selected: boolean;
  onSelect: (id: string) => void;
  onOpenModal: (nodeId: string, tab: NodeDetailTab) => void;
  testIdPrefix?: string | undefined;
}

function stepNodeClassName(selected: boolean): string {
  const base =
    "flex flex-col rounded-md border bg-surface-elevated text-left text-xs text-text-primary shadow-sm ";
  return selected
    ? `${base}border-l-4 border-text-primary`
    : `${base}border-border hover:border-text-muted`;
}

function failedErrorText(task: WorkflowTask): string | null {
  const raw = task.status === "failed" ? task.error : null;
  return raw != null && raw !== "" ? raw : null;
}

function UsageRow({ task, testId }: Readonly<{ task: WorkflowTask; testId: string }>) {
  const text = formatUsageRow(task.input_tokens, task.output_tokens, task.total_cost_usd);
  if (text === null) return null;
  return <span data-testid={testId} className="text-[10px] text-text-muted">{text}</span>;
}

function ChevronIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      fill="currentColor"
      className="h-3 w-3 shrink-0 text-text-muted"
      aria-hidden="true"
      data-testid="step-card-chevron"
    >
      <path
        fillRule="evenodd"
        d="M6.22 3.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L9.94 8 6.22 4.28a.75.75 0 0 1 0-1.06Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function StepMetaRow({ task }: Readonly<{ task: WorkflowTask }>) {
  const duration = computeDuration(task);
  return (
    <span className="flex items-center justify-between gap-2 text-[10px] text-text-muted">
      <span className="truncate">{task.model}</span>
      {duration !== null && <span className="shrink-0">{duration}</span>}
    </span>
  );
}

interface StepSelectButtonProps {
  task: WorkflowTask;
  label: string;
  selected: boolean;
  testId: string;
  onSelect: (id: string) => void;
}

function StepSelectButton({
  task,
  label,
  selected,
  testId,
  onSelect,
}: Readonly<StepSelectButtonProps>) {
  const baseId = useId();
  const statusId = `${baseId}-status`;
  const errorId = `${baseId}-error`;
  const errorText = failedErrorText(task);
  return (
    <button
      type="button"
      aria-label={`Show activity of ${label}`}
      aria-describedby={errorText === null ? statusId : `${statusId} ${errorId}`}
      aria-current={ariaCurrentSelection(selected)}
      onClick={() => onSelect(task.id)}
      className={`flex w-full cursor-pointer flex-col gap-1 rounded-md px-3 pb-1 pt-2 text-left ${SELECTION_FOCUS_CLASS}`}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="truncate font-medium">{task.role}</span>
        <span className="flex shrink-0 items-center gap-1">
          <span id={statusId}>
            <NodeStatusBadge status={task.status} />
          </span>
          <ChevronIcon />
        </span>
      </span>
      <StepMetaRow task={task} />
      <UsageRow task={task} testId={`${testId}-usage`} />
      {errorText !== null && (
        <span
          id={errorId}
          data-testid={`${testId}-error`}
          className="line-clamp-2 text-[10px] text-red-300"
        >
          {errorText}
        </span>
      )}
    </button>
  );
}

export function StepNodeCard({
  task,
  selected,
  onSelect,
  onOpenModal,
  testIdPrefix,
}: Readonly<StepNodeCardProps>) {
  const pfx = testIdPrefix === undefined ? "" : `${testIdPrefix}-`;
  const testId = `workflow-node-${pfx}${task.id}`;
  const label = stepDisplayName(task);
  return (
    <div
      data-testid={testId}
      data-status={task.status}
      style={{ width: NODE_WIDTH }}
      className={stepNodeClassName(selected)}
    >
      <StepSelectButton
        task={task}
        label={label}
        selected={selected}
        testId={testId}
        onSelect={onSelect}
      />
      <StepCardIcons label={label} onOpen={(tab) => onOpenModal(task.id, tab)} />
    </div>
  );
}

interface NodeData extends Record<string, unknown> {
  task: WorkflowTask;
  selected: boolean;
  onSelect: (id: string) => void;
  onOpenModal: (nodeId: string, tab: NodeDetailTab) => void;
  testIdPrefix?: string | undefined;
}

function CustomNode({ data }: Readonly<{ data: NodeData }>) {
  return (
    <StepNodeCard
      task={data.task}
      selected={data.selected}
      onSelect={data.onSelect}
      onOpenModal={data.onOpenModal}
      testIdPrefix={data.testIdPrefix}
    />
  );
}

const NODE_TYPES = { llm: CustomNode } as const;

function buildGraph(
  tasks: readonly WorkflowTask[],
  selectedId: string | null,
  onSelect: (id: string) => void,
  onOpenModal: (nodeId: string, tab: NodeDetailTab) => void,
): { nodes: Node[]; edges: Edge[] } {
  const layout = computeLayout(tasks);
  const nodes: Node[] = layout.map((l) => ({
    id: l.task.id,
    type: "llm",
    position: { x: l.x, y: l.y },
    initialWidth: NODE_WIDTH,
    initialHeight: ROW_HEIGHT,
    focusable: false,
    data: { task: l.task, selected: l.task.id === selectedId, onSelect, onOpenModal },
  }));
  const edges: Edge[] = [];
  for (const task of tasks) {
    for (const dep of task.depends_on!) {
      edges.push({ id: `${dep}->${task.id}`, source: dep, target: task.id });
    }
  }
  return { nodes, edges };
}

export interface WorkflowDAGProps {
  tasks: readonly WorkflowTask[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpenModal: (nodeId: string, tab: NodeDetailTab) => void;
  testId: string;
}

export default function WorkflowDAG({
  tasks,
  selectedId,
  onSelect,
  onOpenModal,
  testId,
}: Readonly<WorkflowDAGProps>) {
  const { nodes, edges } = useMemo(
    () => buildGraph(tasks, selectedId, onSelect, onOpenModal),
    [tasks, selectedId, onSelect, onOpenModal],
  );
  return (
    <div
      className="h-[420px] rounded-md border border-border bg-surface"
      data-testid={testId}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        colorMode="dark"
        fitView
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
