import { summarizeToolUse } from "../../../components/streamSummary";

export function safeJsonParse(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function extractInnerEvent(raw: string): Record<string, unknown> | null {
  const outer = safeJsonParse(raw);
  if (outer === null || typeof outer !== "object") return null;
  const inner = (outer as { event?: unknown }).event;
  if (typeof inner !== "string") return null;
  const parsed = safeJsonParse(inner);
  if (parsed === null || typeof parsed !== "object") return null;
  return parsed as Record<string, unknown>;
}

export function parseToolActivityLabel(raw: string): string | null {
  const inner = extractInnerEvent(raw);
  if (inner === null) return null;
  return summarizeToolUse(inner);
}
