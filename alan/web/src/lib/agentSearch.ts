import type { AgentEntry, AgentKind, AgentPromptSegment } from "../api";

export const AGENT_KINDS: readonly AgentKind[] = [
  "team_role",
  "chat",
  "background_helper",
  "operator_launched",
];

export const AGENT_KIND_LABEL: Readonly<Record<AgentKind, string>> = {
  team_role: "Team role",
  chat: "Chat",
  background_helper: "Background helper",
  operator_launched: "Operator-launched",
};

export interface AgentFilters {
  readonly model: string | null;
  readonly effort: string | null;
  readonly kind: AgentKind | null;
}

export interface MatchRange {
  readonly start: number;
  readonly end: number;
}

export interface AgentHit {
  readonly entry: AgentEntry;
  readonly promptMatches: number;
}

export interface AgentFilterOptions {
  readonly models: readonly string[];
  readonly efforts: readonly string[];
}

export interface PromptPiece {
  readonly text: string;
  readonly segment: AgentPromptSegment;
  readonly match: number | null;
  readonly matchStart: boolean;
  readonly segmentStart: boolean;
}

export function parseAgentKind(raw: string | null): AgentKind | null {
  return AGENT_KINDS.find((kind) => kind === raw) ?? null;
}

export function placeholderToken(label: string): string {
  return `{{${label}}}`;
}

export function segmentText(segment: AgentPromptSegment): string {
  return segment.kind === "placeholder" ? placeholderToken(segment.label) : segment.text;
}

export function promptPlainText(segments: readonly AgentPromptSegment[]): string {
  return segments.map(segmentText).join("");
}

export function findMatchRanges(text: string, term: string): MatchRange[] {
  const needle = term.trim().toLowerCase();
  if (needle === "") return [];
  const haystack = text.toLowerCase();
  const ranges: MatchRange[] = [];
  for (let at = haystack.indexOf(needle); at !== -1; at = haystack.indexOf(needle, at + needle.length)) {
    ranges.push({ start: at, end: at + needle.length });
  }
  return ranges;
}

function sliceSegment(
  segment: AgentPromptSegment,
  offset: number,
  ranges: readonly MatchRange[],
): PromptPiece[] {
  const text = segmentText(segment);
  const local = ranges
    .map((range, match) => ({
      match,
      begins: range.start >= offset,
      start: Math.max(range.start, offset) - offset,
      end: Math.min(range.end, offset + text.length) - offset,
    }))
    .filter((range) => range.start < range.end);
  const pieces: PromptPiece[] = [];
  const add = (from: number, to: number, match: number | null, matchStart = false): void => {
    pieces.push({
      text: text.slice(from, to),
      segment,
      match,
      matchStart,
      segmentStart: pieces.length === 0,
    });
  };
  let cursor = 0;
  for (const range of local) {
    if (range.start > cursor) add(cursor, range.start, null);
    add(range.start, range.end, range.match, range.begins);
    cursor = range.end;
  }
  if (cursor < text.length) add(cursor, text.length, null);
  return pieces;
}

export function promptPieces(
  segments: readonly AgentPromptSegment[],
  ranges: readonly MatchRange[],
): PromptPiece[] {
  const pieces: PromptPiece[] = [];
  let offset = 0;
  for (const segment of segments) {
    pieces.push(...sliceSegment(segment, offset, ranges));
    offset += segmentText(segment).length;
  }
  return pieces;
}

function includesFolded(haystack: string, needle: string): boolean {
  return haystack.toLowerCase().includes(needle.toLowerCase());
}

function metadataMatches(entry: AgentEntry, needle: string): boolean {
  return [entry.name, entry.id, entry.model.display, entry.effort.display].some((field) =>
    includesFolded(field, needle),
  );
}

function passesFilters(entry: AgentEntry, filters: AgentFilters): boolean {
  return (
    (filters.model === null || entry.model.display === filters.model) &&
    (filters.effort === null || entry.effort.display === filters.effort) &&
    (filters.kind === null || entry.kind === filters.kind)
  );
}

function hitFor(entry: AgentEntry, needle: string): AgentHit | null {
  const promptMatches = findMatchRanges(promptPlainText(entry.prompt ?? []), needle).length;
  if (needle !== "" && promptMatches === 0 && !metadataMatches(entry, needle)) return null;
  return { entry, promptMatches };
}

export function searchAgents(
  entries: readonly AgentEntry[],
  query: string,
  filters: AgentFilters,
): AgentHit[] {
  const needle = query.trim();
  return entries
    .filter((entry) => passesFilters(entry, filters))
    .flatMap((entry) => {
      const hit = hitFor(entry, needle);
      return hit === null ? [] : [hit];
    });
}

function sortedUnique(values: readonly string[]): string[] {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b));
}

export function agentFilterOptions(entries: readonly AgentEntry[]): AgentFilterOptions {
  return {
    models: sortedUnique(entries.map((entry) => entry.model.display)),
    efforts: sortedUnique(entries.map((entry) => entry.effort.display)),
  };
}
