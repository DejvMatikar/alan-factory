interface TopoEdge {
  from: string;
  to: string;
}

function buildAdjacency(
  nodeIds: readonly string[],
  edges: readonly TopoEdge[],
): { succ: Map<string, string[]>; preds: Map<string, string[]>; inDeg: Map<string, number> } {
  const valid = new Set(nodeIds);
  const succ = new Map<string, string[]>(nodeIds.map(id => [id, []]));
  const preds = new Map<string, string[]>(nodeIds.map(id => [id, []]));
  const inDeg = new Map<string, number>(nodeIds.map(id => [id, 0]));
  for (const { from, to } of edges) {
    if (!valid.has(from) || !valid.has(to)) continue;
    succ.get(from)!.push(to);
    preds.get(to)!.push(from);
    inDeg.set(to, inDeg.get(to)! + 1);
  }
  return { succ, preds, inDeg };
}

function advanceNeighbors(
  id: string,
  succ: Map<string, string[]>,
  inDeg: Map<string, number>,
  queue: string[],
): void {
  for (const next of succ.get(id)!) {
    inDeg.set(next, inDeg.get(next)! - 1);
    if (inDeg.get(next) === 0) queue.push(next);
  }
}

export function buildTaskRanks(
  nodeIds: readonly string[],
  edges: readonly TopoEdge[],
): Map<string, number> {
  const { succ, preds, inDeg } = buildAdjacency(nodeIds, edges);
  const ranks = new Map<string, number>();
  const queue = nodeIds.filter(id => inDeg.get(id) === 0);
  while (queue.length > 0) {
    const id = queue.shift()!;
    const pr = preds.get(id)!.map(p => ranks.get(p)!);
    ranks.set(id, pr.length > 0 ? Math.max(...pr) + 1 : 0);
    advanceNeighbors(id, succ, inDeg, queue);
  }
  let extra = ranks.size > 0 ? Math.max(...ranks.values()) + 1 : 0;
  for (const id of nodeIds) {
    if (!ranks.has(id)) ranks.set(id, extra++);
  }
  return ranks;
}

export function topoSortTasks<T extends { id: string; depends_on?: readonly string[] }>(
  tasks: readonly T[],
): T[] {
  const ids = tasks.map(t => t.id);
  const edges = tasks.flatMap(t => t.depends_on!.map(dep => ({ from: dep, to: t.id })));
  const { succ, inDeg } = buildAdjacency(ids, edges);
  const byId = new Map(tasks.map(t => [t.id, t]));
  const result: T[] = [];
  const queue = ids.filter(id => inDeg.get(id) === 0);
  while (queue.length > 0) {
    const id = queue.shift()!;
    result.push(byId.get(id)!);
    advanceNeighbors(id, succ, inDeg, queue);
  }
  for (const id of ids) {
    if (!result.some(t => t.id === id)) result.push(byId.get(id)!);
  }
  return result;
}
