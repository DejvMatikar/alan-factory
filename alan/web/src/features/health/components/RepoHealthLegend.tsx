import {
  LONG_WINDOW_DAYS,
  LOW_DATA_MIN_SAMPLE,
  MISSING_VALUE,
  NOT_MEASURED_YET,
  SAME_BAND_POINTS,
  SHORT_WINDOW_DAYS,
  verdictDisplay,
} from "../../../lib/metricWindow";

interface ColumnDefinition {
  readonly term: string;
  readonly definition: string;
}

const COLUMN_DEFINITIONS: readonly ColumnDefinition[] = [
  { term: "Findings", definition: "Open health findings, weighted by severity." },
  { term: "Build success", definition: "Share of build workspaces that finished successfully." },
  {
    term: "30-day outcome",
    definition: "Share of dispatched tasks that ended in a merged, evidenced PR.",
  },
  {
    term: "Autonomous",
    definition: "Share of dispatched tasks that got there with no recorded human intervention.",
  },
  { term: "In flight", definition: "Build workspaces running right now. Not compared over time." },
  {
    term: "First-pass",
    definition: "Share of implementation attempts approved on their first verification.",
  },
];

const BETTER = verdictDisplay("better");
const WORSE = verdictDisplay("worse");
const SAME = verdictDisplay("same");

export default function RepoHealthLegend() {
  return (
    <section
      data-testid="repo-health-legend"
      aria-label="How to read the repository health table"
      className="mt-3 border-t border-border pt-3 text-xs text-text-muted"
    >
      <p data-testid="repo-health-legend-window">
        Rates cover the last {LONG_WINDOW_DAYS} days. The symbol beside a value compares the last{" "}
        {SHORT_WINDOW_DAYS} days with those {LONG_WINDOW_DAYS}: {BETTER.symbol} better, {WORSE.symbol}{" "}
        worse, {SAME.symbol} about the same, meaning within ±{SAME_BAND_POINTS} percentage points.
        No symbol means there is not enough data to compare.
      </p>
      <p data-testid="repo-health-legend-low-data" className="mt-1">
        A dimmed value has fewer than {LOW_DATA_MIN_SAMPLE} samples; n= is the number of samples
        behind it and appears only for those values. Too few samples in either window means no
        symbol.
      </p>
      <p data-testid="repo-health-legend-missing" className="mt-1">
        {MISSING_VALUE} means {NOT_MEASURED_YET}.
      </p>
      <dl data-testid="repo-health-legend-columns" className="mt-2 grid gap-y-1">
        {COLUMN_DEFINITIONS.map(({ term, definition }) => (
          <div key={term}>
            <dt className="inline font-medium text-text-primary">{term}:</dt>{" "}
            <dd className="inline">{definition}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
