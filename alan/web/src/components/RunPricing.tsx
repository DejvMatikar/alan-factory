import { useState } from "react";

import type { RunSummary } from "../api";

const DISTILLED_MAX_LEN = 140;

function distillSystemPrompt(prompt: string): string {
  const firstLine = prompt.split("\n").find((line) => line.trim().length > 0) ?? "";
  const trimmed = firstLine.trim();
  return trimmed.length > DISTILLED_MAX_LEN
    ? `${trimmed.slice(0, DISTILLED_MAX_LEN)}…`
    : trimmed;
}

function formatCost(cost: number | null | undefined): string {
  return cost == null ? "—" : `$${cost.toFixed(4)}`;
}

function formatTokens(tokens: number | null | undefined): string {
  return tokens == null ? "—" : tokens.toLocaleString();
}

function formatSetup(model: string, effort: string | null): string {
  return effort === null ? model : `${model} · ${effort}`;
}

function testId(base: string, suffix: string | undefined): string {
  return suffix === undefined ? base : `${base}-${suffix}`;
}

export interface RunPricingEntry {
  key?: string;
  label?: string;
  runSummary: RunSummary | null | undefined;
}

interface ResolvedRunPricingEntry {
  key: string | undefined;
  label: string | undefined;
  runSummary: RunSummary;
}

interface RunPricingRowProps {
  entry: ResolvedRunPricingEntry;
  suffix: string | undefined;
  showSystemPrompt: boolean;
  showTokens: boolean;
}

interface RunTokenTotalsProps {
  runSummary: RunSummary;
  suffix: string | undefined;
}

function RunTokenTotals({ runSummary, suffix }: Readonly<RunTokenTotalsProps>) {
  return (
    <>
      <span data-testid={testId("run-summary-output-tokens", suffix)}>
        {formatTokens(runSummary.total_output_tokens)} output tokens
      </span>
      <span data-testid={testId("run-summary-input-tokens", suffix)}>
        {formatTokens(runSummary.total_input_tokens)} billed input tokens
      </span>
    </>
  );
}

function toggleAriaLabel(entry: ResolvedRunPricingEntry, expanded: boolean): string {
  const rowName = entry.label ?? formatSetup(entry.runSummary.model, entry.runSummary.effort);
  return `${expanded ? "Hide" : "Show"} system prompt for ${rowName}`;
}

function RunPricingRow({ entry, suffix, showSystemPrompt, showTokens }: Readonly<RunPricingRowProps>) {
  const [expanded, setExpanded] = useState(false);
  const { runSummary } = entry;

  return (
    <div
      data-testid={testId("run-summary-header", suffix)}
      className="flex flex-col gap-2 rounded-md border border-border bg-surface-elevated px-3 py-2 text-xs text-text-muted"
    >
      {entry.label !== undefined && (
        <span
          data-testid={testId("run-summary-label", suffix)}
          className="text-[11px] font-medium uppercase tracking-wide text-text-muted"
        >
          {entry.label}
        </span>
      )}
      <div className="flex items-center justify-between gap-2">
        <span data-testid={testId("run-summary-setup", suffix)} className="font-medium text-text-primary">
          {formatSetup(runSummary.model, runSummary.effort)}
        </span>
        {showSystemPrompt && (
          <button
            type="button"
            data-testid={testId("run-summary-prompt-toggle", suffix)}
            aria-expanded={expanded}
            aria-label={toggleAriaLabel(entry, expanded)}
            onClick={() => setExpanded((v) => !v)}
            className="underline hover:text-text-primary"
          >
            {expanded ? "Hide system prompt" : "System prompt"}
          </button>
        )}
      </div>
      {showSystemPrompt &&
        (expanded ? (
          <pre
            data-testid={testId("run-summary-prompt-full", suffix)}
            className="max-h-64 overflow-y-auto whitespace-pre-wrap text-text-muted"
          >
            {runSummary.system_prompt}
          </pre>
        ) : (
          <p data-testid={testId("run-summary-prompt-distilled", suffix)} className="truncate">
            {distillSystemPrompt(runSummary.system_prompt)}
          </p>
        ))}
      <div
        className="flex flex-wrap items-baseline gap-3"
        data-testid={testId("run-summary-totals", suffix)}
      >
        <span data-testid={testId("run-summary-cost", suffix)} className="text-sm font-semibold text-text-primary">
          {formatCost(runSummary.total_cost_usd)}
        </span>
        {showTokens && <RunTokenTotals runSummary={runSummary} suffix={suffix} />}
      </div>
      {runSummary.tool_counts!.length > 0 && (
        <fieldset
          className="m-0 flex min-w-0 flex-wrap gap-1 border-0 p-0 lg:flex-nowrap lg:overflow-x-auto"
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable so a keyboard-only user can scroll this row past the fold on large screens (axe scrollable-region-focusable)
          tabIndex={0}
          aria-label="Tool usage, scroll horizontally for more"
          data-testid={testId("run-summary-tools", suffix)}
        >
          {runSummary.tool_counts!.map((t) => (
            <span
              key={t.name}
              data-testid={`${testId("run-summary-tool", suffix)}-${t.name}`}
              className="rounded-full border border-border px-2 py-0.5"
            >
              {t.name} × {t.count}
            </span>
          ))}
        </fieldset>
      )}
    </div>
  );
}

function resolveEntries(runs: RunPricingEntry[]): ResolvedRunPricingEntry[] {
  const resolved: ResolvedRunPricingEntry[] = [];
  for (const entry of runs) {
    if (entry.runSummary == null) continue;
    resolved.push({ key: entry.key, label: entry.label, runSummary: entry.runSummary });
  }
  return resolved;
}

interface RunPricingProps {
  runs: RunPricingEntry[];
  showSystemPrompt?: boolean;
  showTokens?: boolean;
}

export default function RunPricing({
  runs,
  showSystemPrompt = true,
  showTokens = true,
}: Readonly<RunPricingProps>) {
  const resolved = resolveEntries(runs);
  const [first, ...rest] = resolved;
  if (first === undefined) return null;

  if (rest.length === 0) {
    return (
      <RunPricingRow
        entry={first}
        suffix={first.key}
        showSystemPrompt={showSystemPrompt}
        showTokens={showTokens}
      />
    );
  }

  return (
    <div data-testid="run-pricing" className="flex flex-col gap-2">
      {resolved.map((entry, index) => (
        <RunPricingRow
          key={entry.key ?? index}
          entry={entry}
          suffix={entry.key ?? String(index)}
          showSystemPrompt={showSystemPrompt}
          showTokens={showTokens}
        />
      ))}
    </div>
  );
}
