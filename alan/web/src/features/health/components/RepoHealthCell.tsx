import type { HealthMetricWindows } from "../../../api";
import { compactCell } from "../../../lib/metricWindow";

function valueClass(lowData: boolean): string {
  return lowData
    ? "text-sm text-text-muted tabular-nums"
    : "text-sm font-medium text-text-primary tabular-nums";
}

export interface RepoHealthCellProps {
  readonly value: number | null;
  readonly sample: number;
  readonly lowData: boolean;
  readonly format: (value: number) => string;
  readonly testId: string;
}

export default function RepoHealthCell({
  value,
  sample,
  lowData,
  format,
  testId,
}: Readonly<RepoHealthCellProps>) {
  const display = value === null ? "—" : format(value);
  return (
    <span className="flex flex-col">
      <span data-testid={testId} className={valueClass(lowData)}>
        {display}
      </span>
      {lowData && (
        <span data-testid={`${testId}-sample`} className="text-xs text-text-muted">
          n={sample}
        </span>
      )}
    </span>
  );
}

export interface RepoWindowCellProps {
  readonly label: string;
  readonly metric: HealthMetricWindows;
  readonly testId: string;
}

export function RepoWindowCell({ label, metric, testId }: Readonly<RepoWindowCellProps>) {
  const cell = compactCell(label, metric);
  return (
    <span className="flex flex-col">
      <span aria-hidden="true" className="flex flex-col">
        <span className="flex items-baseline gap-1">
          <span data-testid={testId} className={valueClass(cell.lowData)}>
            {cell.text}
          </span>
          {cell.symbol !== null && (
            <span data-testid={`${testId}-symbol`} className="text-xs text-text-muted">
              {cell.symbol}
            </span>
          )}
        </span>
        {cell.sampleText !== null && (
          <span data-testid={`${testId}-sample`} className="text-xs text-text-muted">
            {cell.sampleText}
          </span>
        )}
      </span>
      <span className="sr-only">{cell.sentence}</span>
    </span>
  );
}
