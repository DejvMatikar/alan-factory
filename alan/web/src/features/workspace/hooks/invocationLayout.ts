import type { Edge, Node } from "@xyflow/react";

import type { NodeDetailTab, WorkflowTask } from "../../../api";
import {
  COL_WIDTH,
  GROUP_GAP,
  GROUP_PADDING,
  HEADER_HEIGHT,
  NODE_WIDTH,
  ROW_HEIGHT,
  computeLayout,
  type LayoutNode,
} from "./workflow";

const ARROW = { markerEnd: { type: "arrowclosed" as const } } as const;

export interface GroupDims {
  width: number;
  height: number;
}

export interface GroupInfo {
  invId: string;
  team: string;
  status: string;
  duration: string | null;
  stepCount: number;
  expanded: boolean;
  totalCostUsd: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
}

export interface GroupNodeData extends Record<string, unknown> {
  invId: string;
  taskId: string;
  team: string;
  status: string;
  duration: string | null;
  stepCount: number;
  expanded: boolean;
  onToggle: (invId: string) => void;
  totalCostUsd: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
}

export interface StepNodeData extends Record<string, unknown> {
  task: WorkflowTask;
  selected: boolean;
  onSelect: (id: string) => void;
  onOpenModal: (nodeId: string, tab: NodeDetailTab) => void;
  testIdPrefix: string;
}

export function computeGroupDimensionsFromLayout(
  layout: readonly LayoutNode[],
): GroupDims {
  if (layout.length === 0) {
    return {
      width: COL_WIDTH + 2 * GROUP_PADDING,
      height: HEADER_HEIGHT + ROW_HEIGHT + 2 * GROUP_PADDING,
    };
  }
  const maxX = Math.max(...layout.map((n) => n.x)) + COL_WIDTH;
  const maxY = Math.max(...layout.map((n) => n.y)) + ROW_HEIGHT;
  return {
    width: maxX + 2 * GROUP_PADDING,
    height: HEADER_HEIGHT + maxY + 2 * GROUP_PADDING,
  };
}

export function computeGroupDimensions(tasks: readonly WorkflowTask[]): GroupDims {
  return computeGroupDimensionsFromLayout(computeLayout(tasks));
}

export function buildGroupYPositions(
  invIds: readonly string[],
  dims: ReadonlyMap<string, GroupDims>,
  expandedInvIds: ReadonlySet<string>,
): Map<string, number> {
  const positions = new Map<string, number>();
  let y = 0;
  for (const invId of invIds) {
    positions.set(invId, y);
    const isExpanded = expandedInvIds.has(invId);
    const h = isExpanded ? (dims.get(invId)?.height ?? 0) : HEADER_HEIGHT + 2 * GROUP_PADDING;
    y += h + GROUP_GAP;
  }
  return positions;
}

export function buildGroupNodes(
  groupInfos: readonly GroupInfo[],
  yPositions: ReadonlyMap<string, number>,
  dims: ReadonlyMap<string, GroupDims>,
  onToggle: (invId: string) => void,
  taskId: string,
): Node[] {
  return groupInfos.map(
    ({ invId, team, status, duration, stepCount, expanded, totalCostUsd, inputTokens, outputTokens }) => {
      const dim = dims.get(invId) ?? { width: 0, height: 0 };
      const y = yPositions.get(invId) ?? 0;
      const height = expanded ? dim.height : HEADER_HEIGHT + 2 * GROUP_PADDING;
      return {
        id: invId,
        type: "invGroup",
        position: { x: 0, y },
        style: { width: dim.width, height },
        // Explicit dims so React Flow paints the node immediately instead of
        // holding it visibility:hidden until its ResizeObserver measure pass
        // (which the E2E stress sweep's load could starve).
        initialWidth: dim.width,
        initialHeight: height,
        data: {
          invId, taskId, team, status, duration, stepCount, expanded, onToggle,
          totalCostUsd, inputTokens, outputTokens,
        } satisfies GroupNodeData,
      };
    },
  );
}

export function buildStepChildrenFromLayout(
  invId: string,
  layout: readonly LayoutNode[],
  selectedStepId: string | null,
  onSelectStep: (invId: string, stepId: string) => void,
  onOpenModal: (invId: string, nodeId: string, tab: NodeDetailTab) => void,
): Node[] {
  return layout.map(({ task, x, y }) => {
    const onSelect = (id: string) => onSelectStep(invId, id);
    const onOpen = (nodeId: string, tab: NodeDetailTab) => onOpenModal(invId, nodeId, tab);
    return {
      id: `${invId}::${task.id}`,
      type: "step",
      parentId: invId,
      extent: "parent" as const,
      focusable: false,
      position: { x: GROUP_PADDING + x, y: HEADER_HEIGHT + GROUP_PADDING + y },
      initialWidth: NODE_WIDTH,
      initialHeight: ROW_HEIGHT,
      data: {
        task,
        selected: task.id === selectedStepId,
        onSelect,
        onOpenModal: onOpen,
        testIdPrefix: invId,
      } satisfies StepNodeData,
    };
  });
}

export function buildStepChildren(
  invId: string,
  tasks: readonly WorkflowTask[],
  selectedStepId: string | null,
  onSelectStep: (invId: string, stepId: string) => void,
  onOpenModal: (invId: string, nodeId: string, tab: NodeDetailTab) => void,
): Node[] {
  return buildStepChildrenFromLayout(
    invId,
    computeLayout(tasks),
    selectedStepId,
    onSelectStep,
    onOpenModal,
  );
}

export function buildGroupEdges(invIds: readonly string[]): Edge[] {
  const edges: Edge[] = [];
  for (let i = 1; i < invIds.length; i++) {
    const source = invIds[i - 1]!;
    const target = invIds[i]!;
    edges.push({ id: `chain-${source}->${target}`, source, target, ...ARROW });
  }
  return edges;
}

export function buildStepEdges(invId: string, tasks: readonly WorkflowTask[]): Edge[] {
  const edges: Edge[] = [];
  for (const task of tasks) {
    for (const dep of task.depends_on!) {
      edges.push({
        id: `step-${invId}-${dep}->${task.id}`,
        source: `${invId}::${dep}`,
        target: `${invId}::${task.id}`,
        ...ARROW,
      });
    }
  }
  return edges;
}
