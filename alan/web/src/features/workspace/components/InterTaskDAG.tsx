import { useId } from "react";

import type { PlanningState, TaskStatus } from "../../../api";
import { planningStateGlyph, planningStateLabel } from "../hooks/planningState";
import { buildTaskRanks } from "../hooks/taskSort";

interface DagNode {
  id: string;
  label: string;
  planningState?: PlanningState | undefined;
  status?: TaskStatus | undefined;
}

type StatusBucket = "done" | "needs you" | "failed" | "in progress";

const STATUS_DOT_COLOR: Record<StatusBucket, string> = {
  done: "var(--color-status-success-fg)",
  "needs you": "var(--color-status-attention-fg)",
  failed: "var(--color-status-danger-fg)",
  "in progress": "var(--color-status-active-fg)",
};

function taskStatusBucket(status: TaskStatus): StatusBucket {
  if (status === "completed") return "done";
  if (status === "awaiting_input") return "needs you";
  if (status === "failed") return "failed";
  return "in progress";
}

interface DagEdge {
  from: string;
  to: string;
}

export interface InterTaskDAGProps {
  nodes: readonly DagNode[];
  edges: readonly DagEdge[];
}

const CHAR_W = 7;
const PAD_X = 16;
const MIN_W = 80;
const MAX_W = 330;
const NODE_H = 28;
const H_GAP = 60;
const V_GAP = 12;
const LABEL_MAX = 40;
const PAD = 10;

export function truncLabel(label: string): string {
  const chars = [...label];
  if (chars.length <= LABEL_MAX) return label;
  return chars.slice(0, LABEL_MAX).join("") + "…";
}

export function nodeWidth(displayLabel: string): number {
  return Math.min(MAX_W, Math.max(MIN_W, [...displayLabel].length * CHAR_W + PAD_X * 2));
}

function buildPositions(
  nodes: readonly DagNode[],
  ranks: Map<string, number>,
): Map<string, { x: number; y: number; w: number }> {
  const byRank = new Map<number, DagNode[]>();
  for (const node of nodes) {
    const r = ranks.get(node.id)!;
    const group = byRank.get(r) ?? [];
    group.push(node);
    byRank.set(r, group);
  }
  const maxWPerRank = new Map<number, number>();
  for (const [rank, group] of byRank) {
    maxWPerRank.set(rank, Math.max(...group.map(n => nodeWidth(truncLabel(n.label)))));
  }
  const colX = new Map<number, number>();
  let xOffset = PAD;
  for (const r of [...byRank.keys()].sort((a, b) => a - b)) {
    colX.set(r, xOffset);
    xOffset += maxWPerRank.get(r)! + H_GAP;
  }
  const positions = new Map<string, { x: number; y: number; w: number }>();
  for (const [rank, group] of byRank) {
    const w = maxWPerRank.get(rank)!;
    const x = colX.get(rank)!;
    for (let i = 0; i < group.length; i++) {
      positions.set(group[i]!.id, { x, y: i * (NODE_H + V_GAP) + PAD, w });
    }
  }
  return positions;
}

export default function InterTaskDAG({ nodes, edges }: Readonly<InterTaskDAGProps>) {
  const uid = useId();
  const markerId = `dag-arrow-${uid}`;
  if (nodes.length === 0) return null;
  const ranks = buildTaskRanks(nodes.map(n => n.id), edges);
  const pos = buildPositions(nodes, ranks);
  const maxY = Math.max(...[...pos.values()].map(p => p.y)) + NODE_H + PAD;
  const svgW = Math.max(...[...pos.values()].map(p => p.x + p.w)) + PAD;
  return (
    <svg
      role="img"
      data-testid="inter-task-dag"
      viewBox={`0 0 ${svgW} ${maxY}`}
      preserveAspectRatio="xMinYMin meet"
      aria-label="Task dependency graph"
      className="overflow-visible text-text-primary"
      style={{ width: "100%", maxWidth: svgW, height: "auto" }}
    >
      <defs>
        <marker id={markerId} markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto">
          <path d="M0,0 L6,2 L0,4 Z" fill="currentColor" />
        </marker>
      </defs>
      {edges.map(edge => {
        const f = pos.get(edge.from);
        const t = pos.get(edge.to);
        if (!f || !t) return null;
        return (
          <line
            key={`${edge.from}-${edge.to}`}
            x1={f.x + f.w} y1={f.y + NODE_H / 2}
            x2={t.x} y2={t.y + NODE_H / 2}
            stroke="currentColor" strokeWidth="1"
            markerEnd={`url(#${markerId})`}
          />
        );
      })}
      {nodes.map(node => {
        const p = pos.get(node.id);
        /* v8 ignore next */
        if (!p) return null;
        return (
          <g key={node.id} transform={`translate(${p.x},${p.y})`} data-testid={`dag-node-${node.id}`}>
            <title>{node.label}</title>
            <rect
              width={p.w} height={NODE_H} rx={3}
              style={{ fill: "var(--color-surface-elevated)", stroke: "var(--color-border)" }}
              strokeWidth="1"
            />
            <text x={p.w / 2} y={NODE_H / 2} dominantBaseline="middle" textAnchor="middle" fontSize="11" fill="currentColor">
              {truncLabel(node.label)}
            </text>
            {node.planningState != null && (
              <text
                x={p.w - 4} y={9} textAnchor="end" fontSize="8" fill="currentColor"
                data-testid={`dag-planning-${node.id}`}
                aria-label={`planning: ${planningStateLabel(node.planningState)}`}
              >
                <title>{planningStateLabel(node.planningState)}</title>
                {planningStateGlyph(node.planningState)}
              </text>
            )}
            {node.status != null && (
              <g
                data-testid={`dag-status-${node.id}`}
                role="img"
                aria-label={`status: ${taskStatusBucket(node.status)}`}
              >
                <title>{taskStatusBucket(node.status)}</title>
                <circle cx={6} cy={6} r={3} style={{ fill: STATUS_DOT_COLOR[taskStatusBucket(node.status)] }} />
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );
}
