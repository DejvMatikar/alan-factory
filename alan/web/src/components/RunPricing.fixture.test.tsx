import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunSummary, ToolUseCount } from "../api";
import RunPricing from "./RunPricing";

const FIXTURE_PATH = path.resolve(
  process.cwd(),
  "../../tests/fixtures/run_summary_stream.jsonl",
);

interface RawEvent {
  type?: string;
  message?: { content?: { type?: string; name?: string }[] };
  total_cost_usd?: number;
  usage?: { input_tokens?: number; output_tokens?: number };
}

function readFixtureEvents(): RawEvent[] {
  const raw = readFileSync(FIXTURE_PATH, "utf-8");
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => JSON.parse(line) as RawEvent);
}

function accumulateToolCounts(event: RawEvent, rawCounts: Record<string, number>): void {
  if (event.type !== "assistant") return;
  for (const block of event.message?.content ?? []) {
    if (block.type === "tool_use" && typeof block.name === "string") {
      rawCounts[block.name] = (rawCounts[block.name] ?? 0) + 1;
    }
  }
}

interface UsageTotals {
  cost: number;
  output: number;
  input: number;
}

function accumulateUsage(event: RawEvent, totals: UsageTotals): void {
  if (event.type !== "result") return;
  totals.cost += event.total_cost_usd ?? 0;
  totals.output += event.usage?.output_tokens ?? 0;
  totals.input += event.usage?.input_tokens ?? 0;
}

function sortToolCounts(rawCounts: Record<string, number>): ToolUseCount[] {
  return Object.entries(rawCounts)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

function deriveCanonicalTotals(events: RawEvent[]): {
  totalCostUsd: number;
  totalOutputTokens: number;
  totalInputTokens: number;
  toolCounts: ToolUseCount[];
} {
  const rawCounts: Record<string, number> = {};
  const usage: UsageTotals = { cost: 0, output: 0, input: 0 };
  for (const event of events) {
    accumulateToolCounts(event, rawCounts);
    accumulateUsage(event, usage);
  }
  return {
    totalCostUsd: usage.cost,
    totalOutputTokens: usage.output,
    totalInputTokens: usage.input,
    toolCounts: sortToolCounts(rawCounts),
  };
}

describe("RunPricing canonical fixture drift guard", () => {
  it("independently re-derives the same totals pinned in tests/test_run_summary.py", () => {
    const totals = deriveCanonicalTotals(readFixtureEvents());
    expect(totals.totalCostUsd).toBeCloseTo(0.0017, 6);
    expect(totals.totalOutputTokens).toBe(490);
    expect(totals.totalInputTokens).toBe(320);
    expect(totals.toolCounts).toEqual([
      { name: "Read", count: 3 },
      { name: "Bash", count: 1 },
      { name: "Grep", count: 1 },
    ]);
  });

  it("renders the header from a RunSummary built off the canonical fixture totals", () => {
    const totals = deriveCanonicalTotals(readFixtureEvents());
    const runSummary: RunSummary = {
      model: "opus",
      effort: "xhigh",
      system_prompt: "canonical",
      total_cost_usd: totals.totalCostUsd,
      total_output_tokens: totals.totalOutputTokens,
      total_input_tokens: totals.totalInputTokens,
      tool_counts: totals.toolCounts,
    };
    render(<RunPricing runs={[{ runSummary }]} />);
    expect(screen.getByTestId("run-summary-setup")).toHaveTextContent("opus · xhigh");
    expect(screen.getByTestId("run-summary-cost")).toHaveTextContent("$0.0017");
    expect(screen.getByTestId("run-summary-output-tokens")).toHaveTextContent(
      "490 output tokens",
    );
    expect(screen.getByTestId("run-summary-input-tokens")).toHaveTextContent(
      "320 billed input tokens",
    );
    expect(screen.getByTestId("run-summary-tool-Read")).toHaveTextContent("Read × 3");
    expect(screen.getByTestId("run-summary-tool-Bash")).toHaveTextContent("Bash × 1");
    expect(screen.getByTestId("run-summary-tool-Grep")).toHaveTextContent("Grep × 1");
  });
});
