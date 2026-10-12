import { describe, expect, it } from "vitest";

import {
  makeAgentEntry,
  makeAgentEntryWithout,
  makeValue,
  placeholderSegment,
  textSegment,
} from "../test/agentFixtures";
import {
  agentFilterOptions,
  findMatchRanges,
  parseAgentKind,
  placeholderToken,
  promptPieces,
  promptPlainText,
  searchAgents,
  segmentText,
  type AgentFilters,
} from "./agentSearch";

const NO_FILTERS: AgentFilters = { model: null, effort: null, kind: null };

const IMPLEMENTER = makeAgentEntry({
  id: "team_role:implementation/implementer",
  name: "Implementer",
  prompt: [textSegment("Write the code. Write tests."), placeholderSegment("TASK_TITLE", "The task title")],
});
const JUDGE = makeAgentEntry({
  id: "team_role:verification/judge",
  name: "Judge",
  kind: "background_helper",
  model: makeValue("opus", "engine_role"),
  effort: makeValue("CLI default"),
  prompt: [textSegment("Decide whether the diff is acceptable.")],
});
const ENTRIES = [IMPLEMENTER, JUDGE];

describe("parseAgentKind", () => {
  it("returns a known kind and null for anything else", () => {
    expect(parseAgentKind("chat")).toBe("chat");
    expect(parseAgentKind("bogus")).toBeNull();
    expect(parseAgentKind(null)).toBeNull();
  });
});

describe("prompt text", () => {
  it("renders a placeholder as its brace token and joins segments in order", () => {
    expect(placeholderToken("TASK_TITLE")).toBe("{{TASK_TITLE}}");
    expect(segmentText(placeholderSegment("X", "d"))).toBe("{{X}}");
    expect(promptPlainText(IMPLEMENTER.prompt!)).toBe("Write the code. Write tests.{{TASK_TITLE}}");
  });
});

describe("findMatchRanges", () => {
  it("finds every non-overlapping case-insensitive occurrence", () => {
    expect(findMatchRanges("Write write WRITE", "write")).toEqual([
      { start: 0, end: 5 },
      { start: 6, end: 11 },
      { start: 12, end: 17 },
    ]);
    expect(findMatchRanges("aaaa", "aa")).toHaveLength(2);
  });

  it("returns nothing for a blank term and trims the term first", () => {
    expect(findMatchRanges("abc", "")).toEqual([]);
    expect(findMatchRanges("abc", "   ")).toEqual([]);
    expect(findMatchRanges("abc", " b ")).toEqual([{ start: 1, end: 2 }]);
  });
});

describe("promptPieces", () => {
  it("splits plain, marked and placeholder text and numbers matches in order", () => {
    const segments = IMPLEMENTER.prompt!;
    const ranges = findMatchRanges(promptPlainText(segments), "write");
    const pieces = promptPieces(segments, ranges);
    expect(pieces.map((piece) => piece.text).join("")).toBe(promptPlainText(segments));
    expect(pieces.filter((piece) => piece.match !== null).map((piece) => piece.match)).toEqual([0, 1]);
    expect(pieces.at(-1)?.segment.kind).toBe("placeholder");
  });

  it("marks both halves of a match that straddles two segments with the same number", () => {
    const segments = [textSegment("abc"), placeholderSegment("DEF", "d")];
    const ranges = findMatchRanges(promptPlainText(segments), "c{{d");
    const pieces = promptPieces(segments, ranges);
    expect(pieces.filter((piece) => piece.match === 0).map((piece) => piece.text)).toEqual(["c", "{{D"]);
  });

  it("flags only the piece where a straddling match begins as its start", () => {
    const segments = [textSegment("abc"), placeholderSegment("DEF", "d")];
    const ranges = findMatchRanges(promptPlainText(segments), "c{{d");
    const pieces = promptPieces(segments, ranges);
    expect(pieces.filter((piece) => piece.match === 0).map((piece) => piece.matchStart)).toEqual([true, false]);
    expect(pieces.filter((piece) => piece.match === null).some((piece) => piece.matchStart)).toBe(false);
  });

  it("returns no pieces for an empty prompt", () => {
    expect(promptPieces([], [])).toEqual([]);
  });
});

describe("searchAgents", () => {
  it("returns every agent with zero prompt matches for a blank query", () => {
    expect(searchAgents(ENTRIES, "  ", NO_FILTERS).map((hit) => hit.promptMatches)).toEqual([0, 0]);
  });

  it("counts occurrences in the full prompt text, placeholders included", () => {
    const [hit] = searchAgents(ENTRIES, "write", NO_FILTERS);
    expect(hit?.entry.id).toBe(IMPLEMENTER.id);
    expect(hit?.promptMatches).toBe(2);
    expect(searchAgents(ENTRIES, "task_title", NO_FILTERS)[0]?.promptMatches).toBe(1);
  });

  it("matches names, ids, models and efforts even when the prompt has no occurrence", () => {
    expect(searchAgents(ENTRIES, "judge", NO_FILTERS).map((hit) => hit.entry.name)).toEqual(["Judge"]);
    expect(searchAgents(ENTRIES, "verification/judge", NO_FILTERS)).toHaveLength(1);
    expect(searchAgents(ENTRIES, "opus", NO_FILTERS).map((hit) => hit.promptMatches)).toEqual([0]);
    expect(searchAgents(ENTRIES, "cli default", NO_FILTERS).map((hit) => hit.entry.name)).toEqual(["Judge"]);
  });

  it("drops agents with no match anywhere", () => {
    expect(searchAgents(ENTRIES, "zzz", NO_FILTERS)).toEqual([]);
  });

  it("applies the model, effort and kind filters together with the query", () => {
    expect(searchAgents(ENTRIES, "", { ...NO_FILTERS, model: "opus" }).map((hit) => hit.entry.name)).toEqual([
      "Judge",
    ]);
    expect(searchAgents(ENTRIES, "", { ...NO_FILTERS, effort: "high" }).map((hit) => hit.entry.name)).toEqual([
      "Implementer",
    ]);
    expect(
      searchAgents(ENTRIES, "", { ...NO_FILTERS, kind: "background_helper" }).map((hit) => hit.entry.name),
    ).toEqual(["Judge"]);
    expect(searchAgents(ENTRIES, "write", { ...NO_FILTERS, model: "opus" })).toEqual([]);
  });

  it("treats an entry with no prompt segments as having zero matches", () => {
    const bare = makeAgentEntryWithout(["prompt"], { name: "Judge" });
    expect(searchAgents([bare], "judge", NO_FILTERS)[0]?.promptMatches).toBe(0);
  });
});

describe("agentFilterOptions", () => {
  it("lists each distinct model and effort once, sorted, keeping CLI default", () => {
    expect(agentFilterOptions([...ENTRIES, IMPLEMENTER])).toEqual({
      models: ["opus", "sonnet"],
      efforts: ["CLI default", "high"],
    });
  });
});
