import type { RepoHealthRow } from "../../../api";

export const SEVERITY_WEIGHT: Readonly<Record<"critical" | "high" | "medium", number>> = {
  critical: 3,
  high: 2,
  medium: 1,
};

function compareNullableAscending(a: number | null, b: number | null): number {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return a - b;
}

function compareHealthRepoRows(a: RepoHealthRow, b: RepoHealthRow): number {
  const findingsDiff = (b.outstanding_findings.value ?? 0) - (a.outstanding_findings.value ?? 0);
  if (findingsDiff !== 0) return findingsDiff;
  const outcomeDiff = compareNullableAscending(
    a.windows.outcome.d30.value,
    b.windows.outcome.d30.value,
  );
  if (outcomeDiff !== 0) return outcomeDiff;
  return a.repo.localeCompare(b.repo);
}

export function orderedHealthRepoRows(rows: readonly RepoHealthRow[]): RepoHealthRow[] {
  return [...rows].sort(compareHealthRepoRows);
}
