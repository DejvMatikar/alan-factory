import type { WorkflowTask } from "../../../api";

export interface LayoutNode {
  task: WorkflowTask;
  x: number;
  y: number;
}

export const COL_WIDTH = 260;
export const ROW_HEIGHT = 150;
export const HEADER_HEIGHT = 50;
export const NODE_WIDTH = 220;
export const GROUP_PADDING = 20;
export const GROUP_GAP = 40;

export function computeLayout(tasks: readonly WorkflowTask[]): LayoutNode[] {
  const levels = new Map<string, number>();
  const byId = new Map<string, WorkflowTask>();
  for (const t of tasks) byId.set(t.id, t);

  const levelOf = (id: string): number => {
    const cached = levels.get(id);
    if (cached !== undefined) return cached;
    const task = byId.get(id);
    if (!task || task.depends_on!.length === 0) {
      levels.set(id, 0);
      return 0;
    }
    let max = 0;
    for (const dep of task.depends_on!) {
      const lvl = levelOf(dep) + 1;
      if (lvl > max) max = lvl;
    }
    levels.set(id, max);
    return max;
  };

  const indexInLevel = new Map<number, number>();
  return tasks.map((task) => {
    const level = levelOf(task.id);
    const idx = indexInLevel.get(level) ?? 0;
    indexInLevel.set(level, idx + 1);
    return { task, x: level * COL_WIDTH, y: idx * ROW_HEIGHT };
  });
}

export function stepDisplayName(task: Pick<WorkflowTask, "id" | "role">): string {
  return task.role === task.id ? task.role : `${task.role} (${task.id})`;
}

export function resolveStepLabel(tasks: readonly WorkflowTask[], nodeId: string): string {
  const task = tasks.find((t) => t.id === nodeId);
  return task === undefined ? nodeId : stepDisplayName(task);
}

export function mergeNodeUpdate(
  current: readonly WorkflowTask[],
  update: WorkflowTask,
): WorkflowTask[] {
  const idx = current.findIndex((t) => t.id === update.id);
  if (idx === -1) return [...current, update];
  const next = current.slice();
  next[idx] = update;
  return next;
}

export interface NodeUpdate {
  invId: string;
  task: WorkflowTask;
}

export function parseTaskFromEvent(data: string): NodeUpdate | null {
  const payload = parseObject(data);
  if (payload === null) return null;
  if (typeof payload.inv_id !== "string" || payload.inv_id === "") return null;
  if (typeof payload.node !== "string") return null;
  const parsed = parseObject(payload.node);
  if (parsed === null || !hasTaskShape(parsed)) return null;
  return { invId: payload.inv_id, task: parsed as unknown as WorkflowTask };
}

function hasTaskShape(v: Record<string, unknown>): boolean {
  return (
    typeof v.id === "string" &&
    typeof v.role === "string" &&
    typeof v.model === "string" &&
    typeof v.status === "string" &&
    Array.isArray(v.depends_on)
  );
}

export interface StreamPayload {
  invId: string;
  role: string;
  event: Record<string, unknown>;
}

export function parseStreamPayload(data: string): StreamPayload | null {
  const wrapper = parseObject(data);
  if (wrapper === null) return null;
  const { inv_id: invId, role, event } = wrapper;
  if (typeof invId !== "string" || invId === "") return null;
  if (typeof role !== "string" || typeof event !== "string") return null;
  const parsed = parseObject(event);
  if (parsed === null) return null;
  return { invId, role, event: parsed };
}

export function parseObject(data: string): Record<string, unknown> | null {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  return raw as Record<string, unknown>;
}
