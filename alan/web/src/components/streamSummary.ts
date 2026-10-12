import type { StreamEvent, WorkflowStatus, WorkflowTask } from "../api";

function formatToken(value: number | null | undefined): string {
  return value == null ? "—" : String(value);
}

function formatCost(totalCostUsd: number | null | undefined): string {
  return totalCostUsd == null ? "" : ` · $${totalCostUsd.toFixed(4)}`;
}

export function formatUsageSuffix(
  inputTokens: number | null,
  outputTokens: number | null,
  totalCostUsd: number | null = null,
): string {
  if (inputTokens === null && outputTokens === null) return "";
  return ` ${formatToken(inputTokens)}↑/${formatToken(outputTokens)}↓${formatCost(totalCostUsd)}`;
}

export function formatUsageRow(
  inputTokens: number | null | undefined,
  outputTokens: number | null | undefined,
  totalCostUsd: number | null | undefined,
): string | null {
  if (inputTokens == null && outputTokens == null) return null;
  return `${formatToken(inputTokens)}↑ ${formatToken(outputTokens)}↓${formatCost(totalCostUsd)}`;
}

function sumField(
  tasks: readonly WorkflowTask[],
  pick: (t: WorkflowTask) => number | null | undefined,
): number | null {
  let sum = 0;
  let seen = false;
  for (const task of tasks) {
    const v = pick(task);
    if (v != null) {
      sum += v;
      seen = true;
    }
  }
  return seen ? sum : null;
}

export function sumTaskUsage(tasks: readonly WorkflowTask[]): {
  inputTokens: number | null;
  outputTokens: number | null;
  totalCostUsd: number | null;
} {
  return {
    inputTokens: sumField(tasks, (t) => t.input_tokens),
    outputTokens: sumField(tasks, (t) => t.output_tokens),
    totalCostUsd: sumField(tasks, (t) => t.total_cost_usd),
  };
}

export function computeGroupUsage(
  invocationIds: readonly string[],
  resolveTasks: (invocationId: string) => readonly WorkflowTask[],
): {
  inputTokens: number | null;
  outputTokens: number | null;
  totalCostUsd: number | null;
} {
  return sumTaskUsage(invocationIds.flatMap((invId) => resolveTasks(invId)));
}

type GroupStatus = "done" | "in progress" | "failed";

const GROUP_STATUS_RANK: Record<GroupStatus, number> = {
  done: 0,
  "in progress": 1,
  failed: 2,
};

function classifyInvocationStatus(
  status: WorkflowStatus | null | undefined,
): GroupStatus {
  if (status === "failed" || status === "escalated" || status === "interrupted") return "failed";
  if (status === "success") return "done";
  return "in progress";
}

export function aggregateInvocationStatus(
  statuses: readonly (WorkflowStatus | null | undefined)[],
): GroupStatus {
  if (statuses.length === 0) return "in progress";
  return statuses.reduce<GroupStatus>((worst, status) => {
    const current = classifyInvocationStatus(status);
    return GROUP_STATUS_RANK[current] > GROUP_STATUS_RANK[worst] ? current : worst;
  }, "done");
}

const TOOL_INPUT_KEYS: Record<string, string> = {
  Read: "file_path",
  Write: "file_path",
  Edit: "file_path",
  Bash: "command",
  Grep: "pattern",
  Glob: "pattern",
  WebFetch: "url",
  WebSearch: "query",
};

const MAX_LEN = 80;
const TOP_N = 3;

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) : s;
}

function resolveToolSummary(
  name: string,
  input: Record<string, unknown> | undefined,
): string {
  const key = TOOL_INPUT_KEYS[name];
  if (key === undefined) return name;
  const val = input?.[key];
  if (typeof val === "string" && val.length > 0) {
    return `${name}: ${truncate(val, MAX_LEN)}`;
  }
  return name;
}

export function summarizeToolUse(event: StreamEvent): string | null {
  if (event.type !== "assistant") return null;
  const message = (event as { message?: { content?: unknown } }).message;
  const content = message?.content;
  if (!Array.isArray(content)) return null;
  for (const block of content as {
    type?: string;
    name?: string;
    input?: Record<string, unknown>;
  }[]) {
    if (block.type === "tool_use") {
      const name = typeof block.name === "string" ? block.name : "?";
      return resolveToolSummary(name, block.input);
    }
  }
  return null;
}

function summarizeSystem(event: StreamEvent): string {
  const subtype = typeof event.subtype === "string" ? event.subtype : "";
  return subtype === "init" ? "Init" : `system: ${subtype}`;
}

function summarizeUser(event: StreamEvent): string {
  const content = (event as { content?: unknown }).content;
  if (!Array.isArray(content)) return "user";
  for (const block of content as { type?: string; is_error?: boolean }[]) {
    if (block.type === "tool_result") {
      return block.is_error === true ? "✗ tool_result" : "✓ tool_result";
    }
  }
  return "user";
}

function _extractUsageToken(event: StreamEvent, key: string): number | null {
  const usage = event.usage;
  if (typeof usage !== "object" || usage === null) return null;
  const v = (usage as Record<string, unknown>)[key];
  return typeof v === "number" ? v : null;
}

function summarizeResult(event: StreamEvent): string {
  const subtype = typeof event.subtype === "string" ? event.subtype : "";
  const text = typeof event.result === "string"
    ? `Done: ${truncate(event.result, MAX_LEN)}`
    : `Done: ${subtype}`;
  const cost = typeof event.total_cost_usd === "number" ? event.total_cost_usd : null;
  return text + formatUsageSuffix(
    _extractUsageToken(event, "input_tokens"),
    _extractUsageToken(event, "output_tokens"),
    cost,
  );
}

export function summarizeEvent(event: StreamEvent): string {
  const type = typeof event.type === "string" ? event.type : "event";
  if (type === "system") return summarizeSystem(event);
  if (type === "assistant") return summarizeToolUse(event) ?? "Thinking…";
  if (type === "user") return summarizeUser(event);
  if (type === "result") return summarizeResult(event);
  return type;
}

interface ToolCountEntry {
  name: string;
  count: number;
}

interface AggregateResult {
  currentAction: string;
  counts: ToolCountEntry[];
  totalOther: number;
}

function countToolsInEvent(
  event: StreamEvent,
  rawCounts: Record<string, number>,
): void {
  const message = (event as { message?: { content?: unknown } }).message;
  const content = message?.content;
  if (!Array.isArray(content)) return;
  for (const block of content as { type?: string; name?: string }[]) {
    if (block.type === "tool_use" && typeof block.name === "string") {
      rawCounts[block.name] = (rawCounts[block.name] ?? 0) + 1;
    }
  }
}

export function aggregateToolCounts(events: StreamEvent[]): AggregateResult {
  const rawCounts: Record<string, number> = {};
  let currentAction = "";
  for (const event of events) {
    if (event.type !== "result") currentAction = summarizeEvent(event);
    if (event.type === "assistant") countToolsInEvent(event, rawCounts);
  }
  const sorted = Object.entries(rawCounts)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count);
  const counts = sorted.slice(0, TOP_N);
  const totalOther = sorted
    .slice(TOP_N)
    .reduce((sum, t) => sum + t.count, 0);
  return { currentAction, counts, totalOther };
}
