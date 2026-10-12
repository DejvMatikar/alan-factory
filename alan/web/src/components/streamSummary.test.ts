import { describe, expect, it } from "vitest";

import type { StreamEvent } from "../api";
import { makeTask } from "../tests/fixtures/workflowTask";
import {
  aggregateInvocationStatus,
  aggregateToolCounts,
  computeGroupUsage,
  formatUsageRow,
  formatUsageSuffix,
  sumTaskUsage,
  summarizeEvent,
  summarizeToolUse,
} from "./streamSummary";

describe("sumTaskUsage", () => {
  it("returns all-null when tasks array is empty", () => {
    expect(sumTaskUsage([])).toEqual({ inputTokens: null, outputTokens: null, totalCostUsd: null });
  });

  it("returns all-null when every task has null fields", () => {
    const tasks = [makeTask("a"), makeTask("b")];
    expect(sumTaskUsage(tasks)).toEqual({ inputTokens: null, outputTokens: null, totalCostUsd: null });
  });

  it("sums non-null values and returns null for fields where all are null", () => {
    const tasks = [
      makeTask("a", { input_tokens: 100, output_tokens: 50, total_cost_usd: 0.001 }),
      makeTask("b", { input_tokens: 200, output_tokens: 80, total_cost_usd: 0.002 }),
    ];
    expect(sumTaskUsage(tasks)).toEqual({
      inputTokens: 300,
      outputTokens: 130,
      totalCostUsd: 0.003,
    });
  });

  it("treats partial non-null as seen — returns sum, not null", () => {
    const tasks = [
      makeTask("a", { input_tokens: 100, output_tokens: null, total_cost_usd: null }),
      makeTask("b", { input_tokens: null, output_tokens: 50, total_cost_usd: null }),
    ];
    const result = sumTaskUsage(tasks);
    expect(result.inputTokens).toBe(100);
    expect(result.outputTokens).toBe(50);
    expect(result.totalCostUsd).toBeNull();
  });

  it("returns cost sum even when token fields are all null", () => {
    const tasks = [
      makeTask("a", { total_cost_usd: 0.001 }),
      makeTask("b", { total_cost_usd: 0.002 }),
    ];
    const result = sumTaskUsage(tasks);
    expect(result.inputTokens).toBeNull();
    expect(result.outputTokens).toBeNull();
    expect(result.totalCostUsd).toBeCloseTo(0.003);
  });
});

describe("computeGroupUsage", () => {
  it("returns all-null when invocationIds is empty", () => {
    expect(computeGroupUsage([], () => [])).toEqual({
      inputTokens: null,
      outputTokens: null,
      totalCostUsd: null,
    });
  });

  it("sums tasks resolved across multiple invocations", () => {
    const byInv: Record<string, ReturnType<typeof makeTask>[]> = {
      inv_1: [makeTask("a", { input_tokens: 100, output_tokens: 50, total_cost_usd: 0.001 })],
      inv_2: [makeTask("b", { input_tokens: 200, output_tokens: 80, total_cost_usd: 0.002 })],
    };
    const result = computeGroupUsage(["inv_1", "inv_2"], (invId) => byInv[invId] ?? []);
    expect(result.inputTokens).toBe(300);
    expect(result.outputTokens).toBe(130);
    expect(result.totalCostUsd).toBeCloseTo(0.003);
  });

  it("returns all-null when the resolved tasks carry no usage", () => {
    const result = computeGroupUsage(["inv_1"], () => [makeTask("a")]);
    expect(result).toEqual({ inputTokens: null, outputTokens: null, totalCostUsd: null });
  });
});

describe("aggregateInvocationStatus", () => {
  it("returns 'in progress' for an empty statuses array", () => {
    expect(aggregateInvocationStatus([])).toBe("in progress");
  });

  it("returns 'done' when every status is success", () => {
    expect(aggregateInvocationStatus(["success", "success"])).toBe("done");
  });

  it("returns 'in progress' when any status is pending or running", () => {
    expect(aggregateInvocationStatus(["success", "pending"])).toBe("in progress");
    expect(aggregateInvocationStatus(["success", "running"])).toBe("in progress");
  });

  it("treats a null or undefined status as in progress", () => {
    expect(aggregateInvocationStatus([null, undefined])).toBe("in progress");
  });

  it("returns 'failed' when any status is failed, escalated, or interrupted", () => {
    expect(aggregateInvocationStatus(["success", "failed"])).toBe("failed");
    expect(aggregateInvocationStatus(["success", "escalated"])).toBe("failed");
    expect(aggregateInvocationStatus(["success", "interrupted"])).toBe("failed");
  });

  it("lets a failed status win over an in-progress one regardless of order", () => {
    expect(aggregateInvocationStatus(["failed", "running"])).toBe("failed");
    expect(aggregateInvocationStatus(["running", "failed"])).toBe("failed");
  });
});

describe("formatUsageSuffix", () => {
  it("returns empty string when both tokens are null", () => {
    expect(formatUsageSuffix(null, null)).toBe("");
  });

  it("returns formatted string when both tokens are present", () => {
    expect(formatUsageSuffix(100, 250)).toBe(" 100↑/250↓");
  });

  it("uses dash for null input_tokens", () => {
    expect(formatUsageSuffix(null, 250)).toBe(" —↑/250↓");
  });

  it("uses dash for null output_tokens", () => {
    expect(formatUsageSuffix(100, null)).toBe(" 100↑/—↓");
  });

  it("appends cost separator when totalCostUsd is provided", () => {
    expect(formatUsageSuffix(100, 250, 0.0012)).toBe(" 100↑/250↓ · $0.0012");
  });

  it("omits cost when totalCostUsd is null", () => {
    expect(formatUsageSuffix(100, 250, null)).toBe(" 100↑/250↓");
  });
});

describe("formatUsageRow", () => {
  it("returns null when both tokens are null", () => {
    expect(formatUsageRow(null, null, null)).toBeNull();
  });

  it("returns formatted row when tokens are present", () => {
    expect(formatUsageRow(100, 250, null)).toBe("100↑ 250↓");
  });

  it("appends cost with separator when total_cost_usd is non-null", () => {
    expect(formatUsageRow(42, 88, 0.0007)).toBe("42↑ 88↓ · $0.0007");
  });

  it("uses dash for null input_tokens when output is present", () => {
    expect(formatUsageRow(null, 88, null)).toBe("—↑ 88↓");
  });

  it("uses dash for null output_tokens when input is present", () => {
    expect(formatUsageRow(42, null, null)).toBe("42↑ —↓");
  });
});

describe("summarizeToolUse", () => {
  it("returns null for non-assistant event", () => {
    expect(summarizeToolUse({ type: "system" })).toBeNull();
  });

  it("returns null when content is missing", () => {
    expect(summarizeToolUse({ type: "assistant" })).toBeNull();
  });

  it("returns null when content is not an array", () => {
    expect(
      summarizeToolUse({ type: "assistant", message: { content: "text" } }),
    ).toBeNull();
  });

  it("returns null for assistant with empty content array", () => {
    expect(
      summarizeToolUse({ type: "assistant", message: { content: [] } }),
    ).toBeNull();
  });

  it("returns null when no tool_use block found", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: { content: [{ type: "text", text: "hello" }] },
    };
    expect(summarizeToolUse(event)).toBeNull();
  });

  it("returns enriched label when input key matches", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: {
        content: [
          {
            type: "tool_use",
            name: "Read",
            input: { file_path: "src/foo.ts" },
          },
        ],
      },
    };
    expect(summarizeToolUse(event)).toBe("Read: src/foo.ts");
  });

  it("truncates long input values to 80 chars", () => {
    const long = "a".repeat(100);
    const event: StreamEvent = {
      type: "assistant",
      message: {
        content: [{ type: "tool_use", name: "Bash", input: { command: long } }],
      },
    };
    expect(summarizeToolUse(event)).toBe(`Bash: ${"a".repeat(80)}`);
  });

  it("returns bare name when key not in TOOL_INPUT_KEYS", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: {
        content: [
          {
            type: "tool_use",
            name: "UnknownTool",
            input: { foo: "bar" },
          },
        ],
      },
    };
    expect(summarizeToolUse(event)).toBe("UnknownTool");
  });

  it("returns bare name when input val is empty string", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: {
        content: [
          { type: "tool_use", name: "Read", input: { file_path: "" } },
        ],
      },
    };
    expect(summarizeToolUse(event)).toBe("Read");
  });

  it("returns bare name when input val is not a string", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: {
        content: [
          { type: "tool_use", name: "Read", input: { file_path: 42 } },
        ],
      },
    };
    expect(summarizeToolUse(event)).toBe("Read");
  });

  it("returns bare name when input is absent", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: { content: [{ type: "tool_use", name: "Grep" }] },
    };
    expect(summarizeToolUse(event)).toBe("Grep");
  });

  it("returns '?' when block has no name", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: { content: [{ type: "tool_use" }] },
    };
    expect(summarizeToolUse(event)).toBe("?");
  });

  it("returns summary for the first tool_use and ignores subsequent", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: {
        content: [
          { type: "text", text: "thinking" },
          {
            type: "tool_use",
            name: "Read",
            input: { file_path: "foo.ts" },
          },
          {
            type: "tool_use",
            name: "Write",
            input: { file_path: "bar.ts" },
          },
        ],
      },
    };
    expect(summarizeToolUse(event)).toBe("Read: foo.ts");
  });
});

describe("summarizeEvent", () => {
  it("returns 'Init' for system/init", () => {
    expect(summarizeEvent({ type: "system", subtype: "init" })).toBe("Init");
  });

  it("returns 'system: <subtype>' for system with other subtype", () => {
    expect(summarizeEvent({ type: "system", subtype: "other" })).toBe(
      "system: other",
    );
  });

  it("returns 'system: ' for system with non-string subtype", () => {
    expect(summarizeEvent({ type: "system" })).toBe("system: ");
  });

  it("returns tool summary for assistant with tool_use", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: {
        content: [
          {
            type: "tool_use",
            name: "Bash",
            input: { command: "git status" },
          },
        ],
      },
    };
    expect(summarizeEvent(event)).toBe("Bash: git status");
  });

  it("returns 'Thinking…' for assistant with no tool_use", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: { content: [{ type: "text", text: "reasoning" }] },
    };
    expect(summarizeEvent(event)).toBe("Thinking…");
  });

  it("returns '✓ tool_result' for user with successful tool_result", () => {
    const event: StreamEvent = {
      type: "user",
      content: [{ type: "tool_result", is_error: false }],
    };
    expect(summarizeEvent(event)).toBe("✓ tool_result");
  });

  it("returns '✗ tool_result' for user with error tool_result", () => {
    const event: StreamEvent = {
      type: "user",
      content: [{ type: "tool_result", is_error: true }],
    };
    expect(summarizeEvent(event)).toBe("✗ tool_result");
  });

  it("returns 'user' for user event with no tool_result block", () => {
    expect(
      summarizeEvent({ type: "user", content: [{ type: "text" }] }),
    ).toBe("user");
  });

  it("returns 'user' for user event with empty content array", () => {
    expect(summarizeEvent({ type: "user", content: [] })).toBe("user");
  });

  it("returns 'user' for user event with non-array content", () => {
    expect(summarizeEvent({ type: "user", content: "text" })).toBe("user");
  });

  it("returns 'user' for user event with no content field", () => {
    expect(summarizeEvent({ type: "user" })).toBe("user");
  });

  it("returns 'Done: <text>' for result with string result", () => {
    expect(
      summarizeEvent({ type: "result", subtype: "success", result: "all done" }),
    ).toBe("Done: all done");
  });

  it("appends token suffix when result event has usage block", () => {
    expect(
      summarizeEvent({
        type: "result",
        subtype: "success",
        result: "ok",
        usage: { input_tokens: 100, output_tokens: 250 },
      }),
    ).toBe("Done: ok 100↑/250↓");
  });

  it("appends token and cost suffix when result event has usage and total_cost_usd", () => {
    expect(
      summarizeEvent({
        type: "result",
        subtype: "success",
        result: "ok",
        usage: { input_tokens: 100, output_tokens: 50 },
        total_cost_usd: 0.0012,
      }),
    ).toBe("Done: ok 100↑/50↓ · $0.0012");
  });

  it("omits token suffix when usage block is absent", () => {
    expect(
      summarizeEvent({ type: "result", subtype: "success", result: "ok" }),
    ).toBe("Done: ok");
  });

  it("uses dash for non-number token in usage block", () => {
    expect(
      summarizeEvent({
        type: "result",
        result: "ok",
        usage: { input_tokens: "not-a-number", output_tokens: 250 },
      }),
    ).toBe("Done: ok —↑/250↓");
  });

  it("truncates long result strings to 80 chars", () => {
    const long = "x".repeat(100);
    expect(summarizeEvent({ type: "result", result: long })).toBe(
      `Done: ${"x".repeat(80)}`,
    );
  });

  it("returns 'Done: <subtype>' for result with non-string result", () => {
    expect(
      summarizeEvent({
        type: "result",
        subtype: "max_turns",
        result: null,
      }),
    ).toBe("Done: max_turns");
  });

  it("returns 'Done: ' for result with null result and no subtype", () => {
    expect(summarizeEvent({ type: "result", result: null })).toBe("Done: ");
  });

  it("returns the type label for unknown event types", () => {
    expect(summarizeEvent({ type: "weird_type" })).toBe("weird_type");
  });

  it("returns 'event' when type field is absent", () => {
    expect(summarizeEvent({})).toBe("event");
  });

  it("returns 'event' when type is not a string", () => {
    expect(summarizeEvent({ type: 42 } as unknown as StreamEvent)).toBe("event");
  });
});

describe("aggregateToolCounts", () => {
  it("returns empty result for no events", () => {
    const result = aggregateToolCounts([]);
    expect(result.currentAction).toBe("");
    expect(result.counts).toEqual([]);
    expect(result.totalOther).toBe(0);
  });

  it("sets currentAction from the last non-result event", () => {
    const events: StreamEvent[] = [
      { type: "system", subtype: "init" },
      {
        type: "assistant",
        message: {
          content: [
            {
              type: "tool_use",
              name: "Read",
              input: { file_path: "foo.ts" },
            },
          ],
        },
      },
    ];
    expect(aggregateToolCounts(events).currentAction).toBe("Read: foo.ts");
  });

  it("does not update currentAction for result events", () => {
    const events: StreamEvent[] = [
      { type: "system", subtype: "init" },
      { type: "result", subtype: "success", result: "done" },
    ];
    expect(aggregateToolCounts(events).currentAction).toBe("Init");
  });

  it("counts tool_use events by name", () => {
    const events: StreamEvent[] = [
      {
        type: "assistant",
        message: {
          content: [
            {
              type: "tool_use",
              name: "Read",
              input: { file_path: "a.ts" },
            },
          ],
        },
      },
      { type: "user", content: [{ type: "tool_result" }] },
      {
        type: "assistant",
        message: {
          content: [
            {
              type: "tool_use",
              name: "Read",
              input: { file_path: "b.ts" },
            },
          ],
        },
      },
      {
        type: "assistant",
        message: {
          content: [
            { type: "tool_use", name: "Bash", input: { command: "ls" } },
          ],
        },
      },
    ];
    const result = aggregateToolCounts(events);
    expect(result.counts).toEqual([
      { name: "Read", count: 2 },
      { name: "Bash", count: 1 },
    ]);
    expect(result.totalOther).toBe(0);
  });

  it("caps counts at 3 and accumulates the rest in totalOther", () => {
    const make = (name: string): StreamEvent => ({
      type: "assistant",
      message: { content: [{ type: "tool_use", name }] },
    });
    const events = [
      make("Read"),
      make("Read"),
      make("Write"),
      make("Write"),
      make("Write"),
      make("Bash"),
      make("Grep"),
      make("Glob"),
    ];
    const result = aggregateToolCounts(events);
    expect(result.counts).toHaveLength(3);
    expect(result.totalOther).toBeGreaterThan(0);
  });

  it("ignores tool_use blocks without a string name", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: { content: [{ type: "tool_use" }] },
    };
    const result = aggregateToolCounts([event]);
    expect(result.counts).toEqual([]);
    expect(result.totalOther).toBe(0);
  });

  it("ignores non-tool-use content blocks when counting", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: { content: [{ type: "text", text: "thinking" }] },
    };
    const result = aggregateToolCounts([event]);
    expect(result.counts).toEqual([]);
    expect(result.totalOther).toBe(0);
  });

  it("handles assistant events with non-array content", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: { content: null },
    };
    expect(aggregateToolCounts([event]).counts).toEqual([]);
  });

  it("handles assistant events with empty content array", () => {
    const event: StreamEvent = {
      type: "assistant",
      message: { content: [] },
    };
    expect(aggregateToolCounts([event]).counts).toEqual([]);
  });
});
