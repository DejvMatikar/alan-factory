import { formatActiveDuration } from "../../../lib/format";

export type ActiveTimeVariant = "value" | "sum";

interface ActiveTimeCellProps {
  readonly seconds: number | null;
  readonly variant: ActiveTimeVariant;
  readonly testId: string;
}

const EXCLUSION_NOTE =
  "excludes time waiting on a human, external checks, capacity, or idle time";

function valueAriaLabel(compact: string, seconds: number | null): string {
  if (seconds === null) return `Active fleet time not recorded, ${EXCLUSION_NOTE}`;
  return `Active fleet time ${compact}, ${EXCLUSION_NOTE}`;
}

function sumAriaLabel(compact: string, seconds: number | null): string {
  if (seconds === null) return `Sum of active fleet time not recorded, ${EXCLUSION_NOTE}`;
  return `Sum of active fleet time ${compact} across visible items — a sum of effort, not elapsed calendar time, and it still ${EXCLUSION_NOTE}`;
}

export default function ActiveTimeCell({ seconds, variant, testId }: Readonly<ActiveTimeCellProps>) {
  const compact = formatActiveDuration(seconds);
  const label = variant === "sum" ? sumAriaLabel(compact, seconds) : valueAriaLabel(compact, seconds);
  return (
    <span data-testid={testId} aria-label={label}>
      {variant === "sum" && (
        <span aria-hidden="true">{"Σ "}</span>
      )}
      {compact}
    </span>
  );
}
