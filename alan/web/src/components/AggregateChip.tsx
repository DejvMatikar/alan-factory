import type { AggregateState } from "../lib/fold";

// Additive extension for the repo status strip (W1.3): a confirmed-good /
// confirmed-bad / not-yet-confirmed / no-data tone set, layered onto the
// same badge recipe as the shipped needs_you|executing|idle states rather
// than a cloned component. `ChipTone` chips always pass an explicit `label`
// (their text varies with live data); the three legacy states keep their
// fixed LABEL/GLYPH/STYLE lookups untouched.
export type ChipTone = "green" | "red" | "transitional" | "unknown";
export type AggregateChipState = AggregateState | ChipTone;

const LABEL: Record<AggregateState, string> = {
  needs_you: "needs you",
  executing: "executing",
  idle: "idle",
};

const GLYPH: Record<AggregateState, string> = {
  needs_you: "!",
  executing: "▸",
  idle: "·",
};

const STYLE: Record<AggregateState, string> = {
  needs_you: "bg-status-attention-tint/15 text-status-attention-fg",
  executing: "bg-status-neutral-tint/15 text-status-neutral-fg",
  idle: "bg-status-neutral-tint/15 text-status-neutral-fg",
};

const TONE_GLYPH: Record<ChipTone, string> = {
  green: "✓",
  red: "✕",
  transitional: "∼",
  unknown: "—",
};

const TONE_STYLE: Record<ChipTone, string> = {
  green: "bg-status-success-tint/15 text-status-success-fg",
  red: "bg-status-danger-tint/15 text-status-danger-fg",
  transitional: "bg-status-warning-tint/15 text-status-warning-fg",
  unknown: "bg-status-neutral-tint/15 text-status-neutral-fg",
};

function isLegacyState(state: AggregateChipState): state is AggregateState {
  return state === "needs_you" || state === "executing" || state === "idle";
}

export default function AggregateChip({
  state,
  testIdSuffix,
  label,
}: Readonly<{ state: AggregateChipState; testIdSuffix: string; label?: string }>) {
  const legacy = isLegacyState(state);
  const style = legacy ? STYLE[state] : TONE_STYLE[state];
  const glyph = legacy ? GLYPH[state] : TONE_GLYPH[state];
  const text = legacy ? LABEL[state] : (label ?? "");
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs ${style}`}
      data-testid={`aggregate-chip-${testIdSuffix}`}
    >
      {legacy ? text : <span className="tabular-nums">{text}</span>}
      <span className="ml-1" aria-hidden="true">
        {glyph}
      </span>
    </span>
  );
}
