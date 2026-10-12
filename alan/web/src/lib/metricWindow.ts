import type { HealthMetricWindows, HealthVerdict, HealthWindow } from "../api";

export const SHORT_WINDOW_DAYS = 7;
export const LONG_WINDOW_DAYS = 30;
export const LOW_DATA_MIN_SAMPLE = 10;
export const SAME_BAND_POINTS = 5;
export const NOT_MEASURED_YET = "not measured yet";
export const LOW_DATA_LABEL = "low data";
export const MISSING_VALUE = "—";

interface VerdictDisplay {
  readonly symbol: string | null;
  readonly word: string;
}

const VERDICT_DISPLAY: Readonly<Record<HealthVerdict, VerdictDisplay>> = {
  better: { symbol: "▲", word: "Better" },
  worse: { symbol: "▼", word: "Worse" },
  same: { symbol: "≈", word: "About the same" },
  insufficient: { symbol: null, word: "Not enough data to compare" },
};

const VERDICT_CLAUSE: Readonly<Record<HealthVerdict, string>> = {
  better: "the last 7 days are better than the last 30 days",
  worse: "the last 7 days are worse than the last 30 days",
  same: `the last 7 days are about the same as the last 30 days, within ${SAME_BAND_POINTS} percentage points`,
  insufficient: "there is not enough data to compare the last 7 days with the last 30 days",
};

export interface UnmeasuredReading {
  readonly measured: false;
}

export interface MeasuredReading {
  readonly measured: true;
  readonly value: number;
  readonly percent: string;
  readonly sample: number;
  readonly lowData: boolean;
}

export type WindowReading = UnmeasuredReading | MeasuredReading;

export interface FunnelReading {
  readonly measured: boolean;
  readonly text: string;
  readonly lowData: boolean;
}

export interface CompactCell {
  readonly text: string;
  readonly sampleText: string | null;
  readonly lowData: boolean;
  readonly symbol: string | null;
  readonly sentence: string;
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function readWindow(window: HealthWindow): WindowReading {
  const { value, sample } = window;
  if (value === null || sample <= 0) return { measured: false };
  return {
    measured: true,
    value,
    percent: formatPercent(value),
    sample,
    lowData: sample < LOW_DATA_MIN_SAMPLE,
  };
}

export function readFunnel(count: number, dispatched: number): FunnelReading {
  if (dispatched <= 0) return { measured: false, text: NOT_MEASURED_YET, lowData: false };
  return {
    measured: true,
    text: `${count} of ${dispatched}`,
    lowData: dispatched < LOW_DATA_MIN_SAMPLE,
  };
}

export function coverageNote(window: HealthWindow, days: number): string | null {
  if (window.sample <= 0 || window.covered_days >= days) return null;
  if (window.covered_days < 1) return "covers under 1 day";
  const unit = window.covered_days === 1 ? "day" : "days";
  return `covers only ${window.covered_days} ${unit}`;
}

export function verdictDisplay(verdict: HealthVerdict): VerdictDisplay {
  return VERDICT_DISPLAY[verdict];
}

function windowText(window: HealthWindow, days: number): string {
  const reading = readWindow(window);
  if (!reading.measured) return NOT_MEASURED_YET;
  const parts = [reading.percent, `n=${reading.sample}`];
  if (reading.lowData) parts.push(LOW_DATA_LABEL);
  const note = coverageNote(window, days);
  if (note !== null) parts.push(note);
  return parts.join(", ");
}

function directionHint(metric: HealthMetricWindows): string {
  const decided = metric.verdict === "better" || metric.verdict === "worse";
  return decided && !metric.higher_is_better ? " (lower is better for this metric)" : "";
}

export function comparisonSentence(label: string, metric: HealthMetricWindows): string {
  const seven = windowText(metric.d7, SHORT_WINDOW_DAYS);
  const thirty = windowText(metric.d30, LONG_WINDOW_DAYS);
  const clause = VERDICT_CLAUSE[metric.verdict];
  return `${label}: last 7 days ${seven}; last 30 days ${thirty}; ${clause}${directionHint(metric)}.`;
}

export function compactCell(label: string, metric: HealthMetricWindows): CompactCell {
  const reading = readWindow(metric.d30);
  const lowData = reading.measured && reading.lowData;
  return {
    text: reading.measured ? reading.percent : MISSING_VALUE,
    sampleText: reading.measured && lowData ? `n=${reading.sample}` : null,
    lowData,
    symbol: verdictDisplay(metric.verdict).symbol,
    sentence: comparisonSentence(label, metric),
  };
}

export function oldestTimestamp(values: readonly (string | null)[]): string | null {
  return values.reduce<string | null>(
    (oldest, next) =>
      next !== null && (oldest === null || Date.parse(next) < Date.parse(oldest)) ? next : oldest,
    null,
  );
}
