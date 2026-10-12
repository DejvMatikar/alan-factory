import type { ProjectListEntry, WorkspaceSummary } from "../../../api";

export const EMPTY_CELL = "—";

export function formatUsd(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

export function formatCostCell(usd: number | null | undefined): string {
  return usd == null ? EMPTY_CELL : `$${usd.toFixed(4)}`;
}

type RoadmapCounts = Pick<ProjectListEntry, "roadmap_items_total" | "roadmap_items_delivered">;

function roadmapTotal(project: RoadmapCounts): number {
  return project.roadmap_items_total;
}

function roadmapDelivered(project: RoadmapCounts): number {
  return project.roadmap_items_delivered;
}

export function phaseCellForProject(project: RoadmapCounts): string {
  const total = roadmapTotal(project);
  if (total === 0) return "planning · no plan yet";
  return `roadmap · ${String(roadmapDelivered(project))} of ${String(total)} items`;
}

export function doneCellForProject(project: RoadmapCounts): string {
  const total = roadmapTotal(project);
  return total === 0 ? EMPTY_CELL : `${String(roadmapDelivered(project))}/${String(total)}`;
}

type FeatureTaskCounts = Pick<WorkspaceSummary, "tasks_total" | "tasks_done">;

export function doneCellForFeature(ws: FeatureTaskCounts): string {
  return `${String(ws.tasks_done)}/${String(ws.tasks_total)}`;
}

export function featurePrimaryLabel(ws: Pick<WorkspaceSummary, "task_id" | "title">): string {
  return `${ws.task_id} · ${ws.title}`;
}

export function noteFirstLine(ws: Pick<WorkspaceSummary, "note">): string | null {
  const normalized = (ws.note ?? "").replaceAll("\r", "");
  const newlineIndex = normalized.indexOf("\n");
  const firstLine = newlineIndex === -1 ? normalized : normalized.slice(0, newlineIndex);
  const line = firstLine.trim();
  return line === "" ? null : line;
}

type FeatureActiveSeconds = Pick<WorkspaceSummary, "active_seconds">;

export function activeSecondsForFeature(ws: FeatureActiveSeconds): number | null {
  return ws.active_seconds ?? null;
}
