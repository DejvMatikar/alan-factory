import {
  Background,
  Controls,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type DispatchInstructionsMap,
  type DispatchInstructionsReadback,
  type Handover,
  type NodeDetailTab,
  type NodeStatus,
  type WorkflowDoc,
  type WorkflowTask,
} from "../../../api";
import { formatTeamLabel } from "../hooks/teamLabel";
import {
  buildGroupNodes,
  buildGroupYPositions,
  buildStepChildrenFromLayout,
  buildStepEdges,
  computeGroupDimensionsFromLayout,
  type GroupDims,
  type GroupInfo,
  type GroupNodeData,
  type StepNodeData,
} from "../hooks/invocationLayout";
import type { DocsState } from "../hooks/useInvocationDocs";
import { resolveHandoverTargetLabel, useHandovers } from "../hooks/useHandovers";
import Button from "../../../components/Button";
import DescribedIconButton from "../../../components/DescribedIconButton";
import NodeStatusBadge from "../../../components/NodeStatusBadge";
import { useAutoScroll } from "../../../hooks/useAutoScroll";
import {
  GROUP_GAP,
  GROUP_PADDING,
  HEADER_HEIGHT,
  computeLayout,
  resolveStepLabel,
  type LayoutNode,
} from "../hooks/workflow";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import {
  pickTasks,
  renderInvocationDuration,
  statusForBadge,
  UsageLineSpan,
  type DocState,
} from "./InvocationCard";
import HandoverDetailPanel from "./HandoverDetailPanel";
import InstructionBadge from "./InstructionBadge";
import NodeDetailModal from "./NodeDetailModal";
import StreamPanel, { type TaggedEvent } from "./StreamPanel";
import { sumTaskUsage } from "../../../components/streamSummary";
import { StepNodeCard } from "./WorkflowDAG";

const DEFAULT_EDGE_OPTIONS = { markerEnd: { type: "arrowclosed" as const } } as const;
const EMPTY_UPDATES: readonly WorkflowTask[] = Object.freeze([]);

const HANDOVER_WIDTH = 120;
const HANDOVER_HEIGHT = 40;

type SelectionState =
  | { kind: "group"; invId: string; stepId: string | null }
  | { kind: "handover"; handoverId: string }
  | null;

interface GraphModalState {
  invId: string;
  nodeId: string;
  tab: NodeDetailTab;
}

interface PanelData {
  invId: string;
  nodeId: string;
  panelRole: string;
  panelError: string | null | undefined;
  liveEvents: readonly TaggedEvent[];
  status: NodeStatus;
}

interface HandoverNodeData extends Record<string, unknown> {
  handover: Handover;
  onSelect: (handoverId: string) => void;
}

interface InvLayout {
  tasks: WorkflowTask[];
  layout: LayoutNode[];
  dims: GroupDims;
}

function precomputeInvLayouts(
  invIds: readonly string[],
  docs: ReadonlyMap<string, DocState | null>,
  live: LiveWorkspaceState,
): Map<string, InvLayout> {
  const result = new Map<string, InvLayout>();
  for (const invId of invIds) {
    const doc = docs.get(invId) ?? null;
    const nodeUpdates = live.invocations[invId]?.nodeUpdates ?? EMPTY_UPDATES;
    const tasks = pickTasks(doc, nodeUpdates);
    const layout = computeLayout(tasks);
    const dims = computeGroupDimensionsFromLayout(layout);
    result.set(invId, { tasks, layout, dims });
  }
  return result;
}

function deriveGroupTeam(doc: DocState | null, hasError: boolean): string {
  if (hasError) return "load failed";
  if (doc === null) return "loading…";
  return formatTeamLabel(doc.team);
}

function deriveGroupStatus(doc: DocState | null, hasError: boolean): string {
  if (hasError) return "error";
  if (doc === null) return "loading";
  return doc.status;
}

function buildGroupInfos(
  invIds: readonly string[],
  docs: ReadonlyMap<string, DocState | null>,
  errors: ReadonlyMap<string, string | null>,
  invLayouts: ReadonlyMap<string, InvLayout>,
  expandedInvIds: ReadonlySet<string>,
): GroupInfo[] {
  return invIds.map((invId) => {
    const doc = docs.get(invId) ?? null;
    const hasError = (errors.get(invId) ?? null) !== null;
    const entry = invLayouts.get(invId)!;
    const duration = doc === null ? null : renderInvocationDuration(doc);
    const usage = sumTaskUsage(entry.tasks);
    return {
      invId,
      team: deriveGroupTeam(doc, hasError),
      status: deriveGroupStatus(doc, hasError),
      duration,
      stepCount: entry.tasks.length,
      expanded: expandedInvIds.has(invId),
      totalCostUsd: usage.totalCostUsd,
      inputTokens: usage.inputTokens,
      outputTokens: usage.outputTokens,
    };
  });
}

function handoverBorderClass(type: Handover["handover_type"]): string {
  if (type === "clarification_request") return "border-yellow-700 text-yellow-400";
  if (type === "revisit_amend") return "border-orange-700 text-orange-400";
  if (type === "parallel_fanout_join") return "border-blue-700 text-blue-400";
  if (type === "escalation") return "border-red-700 text-red-400";
  if (type === "terminal") return "border-green-500 text-green-400";
  return "border-border text-text-muted";
}

function HandoverNode({ data }: Readonly<{ data: HandoverNodeData }>) {
  const { handover, onSelect } = data;
  const toTeam = resolveHandoverTargetLabel(handover);
  const colorClass = handoverBorderClass(handover.handover_type);
  return (
    <button
      type="button"
      data-testid={`handover-node-${handover.handover_id}`}
      onClick={() => onSelect(handover.handover_id)}
      className={`flex h-full w-full items-center gap-1.5 truncate rounded-md border bg-surface-elevated px-2 py-1 font-mono text-[10px] ${colorClass}`}
    >
      <span className="truncate">{handover.from_team} → {toTeam}</span>
      <span className="shrink-0 rounded bg-surface px-1">iter {handover.iteration}</span>
    </button>
  );
}

function buildHandoverNodes(
  handovers: readonly Handover[],
  invIds: readonly string[],
  yPositions: ReadonlyMap<string, number>,
  dimsMap: ReadonlyMap<string, GroupDims>,
  expandedSet: ReadonlySet<string>,
  onSelectHandover: (handoverId: string) => void,
): Node[] {
  const invIdSet = new Set(invIds);
  const nodes: Node[] = [];
  for (const handover of handovers) {
    const anchorId = handover.from_invocation_id;
    if (!invIdSet.has(anchorId)) continue;
    const y = yPositions.get(anchorId)!;
    const dim = dimsMap.get(anchorId)!;
    const anchorH = expandedSet.has(anchorId) ? dim.height : HEADER_HEIGHT + 2 * GROUP_PADDING;
    nodes.push({
      id: `handover-${handover.handover_id}`,
      type: "handover",
      position: {
        x: dim.width + GROUP_GAP,
        y: y + anchorH / 2 - HANDOVER_HEIGHT / 2,
      },
      style: { width: HANDOVER_WIDTH, height: HANDOVER_HEIGHT },
      initialWidth: HANDOVER_WIDTH,
      initialHeight: HANDOVER_HEIGHT,
      data: { handover, onSelect: onSelectHandover } satisfies HandoverNodeData,
    });
  }
  return nodes;
}

function injectHandoverEdges(
  invIds: readonly string[],
  handovers: readonly Handover[],
): Edge[] {
  const byAnchor = new Map<string, Handover>();
  for (const h of handovers) {
    byAnchor.set(h.from_invocation_id, h);
  }
  const edges: Edge[] = [];
  for (let i = 1; i < invIds.length; i++) {
    const source = invIds[i - 1]!;
    const target = invIds[i]!;
    const h = byAnchor.get(source);
    if (h) {
      const hId = `handover-${h.handover_id}`;
      edges.push(
        { id: `chain-${source}->${hId}`, source, target: hId, ...DEFAULT_EDGE_OPTIONS },
        { id: `chain-${hId}->${target}`, source: hId, target, ...DEFAULT_EDGE_OPTIONS },
      );
    } else {
      edges.push({ id: `chain-${source}->${target}`, source, target, ...DEFAULT_EDGE_OPTIONS });
    }
  }
  return edges;
}

interface GraphCallbacks {
  onToggleGroup: (invId: string) => void;
  onSelectStep: (invId: string, stepId: string) => void;
  onSelectHandover: (handoverId: string) => void;
  onOpenModal: (invId: string, nodeId: string, tab: NodeDetailTab) => void;
}

function withInstructionReadbacks(
  nodes: readonly Node[],
  readbacks: DispatchInstructionsMap,
): Node[] {
  return nodes.map((node) => ({
    ...node,
    data: { ...node.data, instructionReadback: readbacks[node.id] },
  }));
}

function buildUnifiedGraph(
  taskId: string,
  groupInfos: readonly GroupInfo[],
  invLayouts: ReadonlyMap<string, InvLayout>,
  handovers: readonly Handover[],
  selection: SelectionState,
  readbacks: DispatchInstructionsMap,
  callbacks: GraphCallbacks,
): { nodes: Node[]; edges: Edge[] } {
  const { onToggleGroup, onSelectStep, onSelectHandover, onOpenModal } = callbacks;
  const invIds = groupInfos.map((g) => g.invId);
  const dimsMap = new Map<string, GroupDims>();
  for (const invId of invIds) {
    dimsMap.set(invId, invLayouts.get(invId)!.dims);
  }
  const expandedSet = new Set(groupInfos.filter((g) => g.expanded).map((g) => g.invId));
  const yPositions = buildGroupYPositions(invIds, dimsMap, expandedSet);
  const groupNodes = withInstructionReadbacks(
    buildGroupNodes(groupInfos, yPositions, dimsMap, onToggleGroup, taskId),
    readbacks,
  );
  const handoverNodes = buildHandoverNodes(handovers, invIds, yPositions, dimsMap, expandedSet, onSelectHandover);
  const groupEdges = injectHandoverEdges(invIds, handovers);
  const stepNodes: Node[] = [];
  const stepEdges: Edge[] = [];
  for (const invId of invIds) {
    if (!expandedSet.has(invId)) continue;
    const entry = invLayouts.get(invId)!;
    const stepId = selection?.kind === "group" && selection.invId === invId ? selection.stepId : null;
    stepNodes.push(
      ...buildStepChildrenFromLayout(invId, entry.layout, stepId, onSelectStep, onOpenModal),
    );
    stepEdges.push(...buildStepEdges(invId, entry.tasks));
  }
  return {
    nodes: [...groupNodes, ...handoverNodes, ...stepNodes],
    edges: [...groupEdges, ...stepEdges],
  };
}

function invGroupClassName(expanded: boolean): string {
  const base =
    "flex min-w-0 flex-1 flex-col gap-1 rounded-md border bg-surface-elevated px-3 py-2 text-left text-xs text-text-primary shadow-sm ";
  return expanded
    ? `${base}border-text-primary ring-1 ring-text-primary`
    : `${base}border-border hover:border-text-muted`;
}

interface GroupNodeDataWithReadback extends GroupNodeData {
  instructionReadback: DispatchInstructionsReadback | undefined;
}

function InvGroupToggle({ data }: Readonly<{ data: GroupNodeDataWithReadback }>) {
  const showBadge = data.status !== "loading" && data.status !== "error";
  return (
    <button
      type="button"
      data-testid={`invocation-node-${data.invId}`}
      data-status={data.status}
      aria-expanded={data.expanded}
      aria-label={`${data.expanded ? "Collapse" : "Expand"} invocation ${data.invId}`}
      onClick={() => data.onToggle(data.invId)}
      className={invGroupClassName(data.expanded)}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">{formatTeamLabel(data.team)}</span>
        {showBadge && (
          <NodeStatusBadge
            status={statusForBadge(data.status as WorkflowDoc["status"])}
          />
        )}
      </div>
      {data.instructionReadback !== undefined && (
        <InstructionBadge invId={data.invId} readback={data.instructionReadback} showDetails={false} />
      )}
      <span className="font-mono text-[10px] text-text-muted">{data.invId}</span>
      {data.duration !== null && (
        <span className="text-[10px] text-text-muted">{data.duration}</span>
      )}
      {data.stepCount > 0 && (
        <span className="text-[10px] text-text-muted">
          {data.stepCount} step{data.stepCount === 1 ? "" : "s"}
        </span>
      )}
      <UsageLineSpan
        testId={`invocation-node-${data.invId}-usage`}
        inputTokens={data.inputTokens}
        outputTokens={data.outputTokens}
        totalCostUsd={data.totalCostUsd}
      />
    </button>
  );
}

function InvGroupNode({ data }: Readonly<{ data: GroupNodeDataWithReadback }>) {
  return (
    <div className="flex w-full items-start gap-1">
      <InvGroupToggle data={data} />
      <Button
        variant="ghost"
        density="compact"
        linkTo={`/workspaces/${encodeURIComponent(data.taskId)}/invocations/${encodeURIComponent(data.invId)}`}
        aria-label={`Open full view of run ${data.invId}`}
        data-testid={`invocation-full-view-${data.invId}`}
        className="nodrag nopan shrink-0"
      >
        Open full view
      </Button>
    </div>
  );
}

function StepNodeWrapper({ data }: Readonly<{ data: StepNodeData }>) {
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

const NODE_TYPES = { invGroup: InvGroupNode, step: StepNodeWrapper, handover: HandoverNode } as const;

function FitViewOnSettle({ allSettled }: Readonly<{ allSettled: boolean }>) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (!allSettled) return;
    fitView().catch(() => undefined);
  }, [allSettled, fitView]);
  return null;
}

function resolveSelectedTask(
  selection: SelectionState,
  invLayouts: ReadonlyMap<string, InvLayout>,
): WorkflowTask | null {
  if (selection?.kind !== "group") return null;
  const entry = invLayouts.get(selection.invId);
  if (entry === undefined) return null;
  return entry.tasks.find((t) => t.id === selection.stepId) ?? null;
}

function resolvePanelData(
  selection: SelectionState,
  selectedTask: WorkflowTask | null,
  live: LiveWorkspaceState,
): PanelData | null {
  if (selection?.kind !== "group" || selection.stepId == null || selectedTask === null) return null;
  const panelRole = selectedTask.role;
  const panelError = selectedTask.status === "failed" ? selectedTask.error : null;
  const liveEvents = live.invocations[selection.invId]?.liveStreams[panelRole] ?? [];
  return {
    invId: selection.invId,
    nodeId: selection.stepId,
    panelRole,
    panelError,
    liveEvents,
    status: selectedTask.status,
  };
}

function resolveSelectedHandover(
  selection: SelectionState,
  handovers: readonly Handover[],
): Handover | null {
  if (selection?.kind !== "handover") return null;
  return handovers.find((h) => h.handover_id === selection.handoverId) ?? null;
}

interface StreamDetailProps {
  taskId: string;
  panel: PanelData;
  onClose: () => void;
}

function StreamDetail({ taskId, panel, onClose }: Readonly<StreamDetailProps>) {
  const detailRef = useAutoScroll([panel.invId, panel.nodeId], "nearest");
  return (
    <div ref={detailRef} data-testid={`stream-detail-${panel.invId}`}>
      <div className="flex justify-end">
        <DescribedIconButton
          glyph="✕"
          label="Close stream panel"
          description="Close this step's event stream"
          testId="stream-panel-close"
          align="end"
          onClick={onClose}
        />
      </div>
      <StreamPanel
        key={`${taskId}:${panel.invId}:${panel.nodeId}`}
        taskId={taskId}
        invId={panel.invId}
        role={panel.panelRole}
        liveEvents={panel.liveEvents}
        nodeError={panel.panelError}
        nodeStatus={panel.status}
      />
    </div>
  );
}

interface GraphModalProps {
  taskId: string;
  modal: GraphModalState;
  invLayouts: ReadonlyMap<string, InvLayout>;
  onClose: () => void;
}

function GraphModal({ taskId, modal, invLayouts, onClose }: Readonly<GraphModalProps>) {
  const tasks = invLayouts.get(modal.invId)?.tasks ?? EMPTY_UPDATES;
  return (
    <NodeDetailModal
      taskId={taskId}
      invId={modal.invId}
      nodeId={modal.nodeId}
      stepLabel={resolveStepLabel(tasks, modal.nodeId)}
      initialTab={modal.tab}
      onClose={onClose}
    />
  );
}

export interface InvocationGraphProps {
  taskId: string;
  invocations: readonly string[];
  live: LiveWorkspaceState;
  docsState: DocsState;
  readbacks: DispatchInstructionsMap;
  repo: string | null;
}

export default function InvocationGraph({
  taskId,
  invocations,
  live,
  docsState,
  readbacks,
  repo,
}: Readonly<InvocationGraphProps>) {
  const [selection, setSelection] = useState<SelectionState>(null);
  const [expandedInvIds, setExpandedInvIds] = useState(() => new Set<string>());
  const [graphModal, setGraphModal] = useState<GraphModalState | null>(null);

  const handleToggleGroup = useCallback(
    (invId: string) => {
      setExpandedInvIds((prev) => {
        const next = new Set(prev);
        if (next.has(invId)) next.delete(invId);
        else next.add(invId);
        return next;
      });
      setSelection((prev) => (prev?.kind === "group" && prev.invId === invId ? null : prev));
    },
    [],
  );
  const handleSelectStep = useCallback(
    (invId: string, stepId: string) => setSelection({ kind: "group", invId, stepId }),
    [],
  );
  const handleClosePanel = useCallback(() => setSelection(null), []);
  const handleSelectHandover = useCallback(
    (handoverId: string) => setSelection({ kind: "handover", handoverId }),
    [],
  );
  const handleOpenModal = useCallback(
    (invId: string, nodeId: string, tab: NodeDetailTab) =>
      setGraphModal({ invId, nodeId, tab }),
    [],
  );
  const handleCloseModal = useCallback(() => setGraphModal(null), []);

  const { docs, errors, allSettled } = docsState;
  const { handovers, error: handoverError } = useHandovers(taskId, invocations);
  const invKey = invocations.join(",");

  const invLayouts = useMemo(
    () => precomputeInvLayouts(invocations, docs, live),
    [invocations, docs, live],
  );

  const groupInfos = useMemo(
    () =>
      buildGroupInfos(
        invocations,
        docs,
        errors,
        invLayouts,
        expandedInvIds,
      ),
    [invocations, docs, errors, invLayouts, expandedInvIds],
  );

  const { nodes, edges } = useMemo(
    () =>
      buildUnifiedGraph(
        taskId,
        groupInfos,
        invLayouts,
        handovers,
        selection,
        readbacks,
        {
          onToggleGroup: handleToggleGroup,
          onSelectStep: handleSelectStep,
          onSelectHandover: handleSelectHandover,
          onOpenModal: handleOpenModal,
        },
      ),
    [taskId, groupInfos, invLayouts, handovers, selection, readbacks, handleToggleGroup, handleSelectStep, handleSelectHandover, handleOpenModal],
  );

  const selectedTask = resolveSelectedTask(selection, invLayouts);
  const panel = resolvePanelData(selection, selectedTask, live);
  const selectedHandover = resolveSelectedHandover(selection, handovers);

  if (invocations.length === 0) {
    return (
      <p className="text-sm text-text-muted" data-testid="invocation-graph-empty">
        No dispatches yet.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div
        className="h-[500px] rounded-md border border-border bg-surface"
        data-testid="invocation-graph"
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
          colorMode="dark"
        >
          <Background />
          <Controls showInteractive={false} />
          <FitViewOnSettle key={invKey} allSettled={allSettled} />
        </ReactFlow>
      </div>
      {handoverError !== null && (
        <p className="text-xs text-red-400" data-testid="handover-load-error">
          handover load failed
        </p>
      )}
      {panel !== null && (
        <StreamDetail taskId={taskId} panel={panel} onClose={handleClosePanel} />
      )}
      {selectedHandover !== null && (
        <div data-testid="handover-panel">
          <div className="flex justify-end">
            <DescribedIconButton
              glyph="✕"
              label="Close panel"
              description="Close the handover details"
              testId="handover-panel-close"
              align="end"
              onClick={handleClosePanel}
            />
          </div>
          <HandoverDetailPanel handover={selectedHandover} repo={repo} />
        </div>
      )}
      {graphModal !== null && (
        <GraphModal
          taskId={taskId}
          modal={graphModal}
          invLayouts={invLayouts}
          onClose={handleCloseModal}
        />
      )}
    </div>
  );
}
