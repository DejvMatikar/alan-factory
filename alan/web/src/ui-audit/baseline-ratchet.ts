export interface DimensionComparison<D extends string> {
  readonly dimension: D;
  readonly baselineCount: number;
  readonly currentCount: number;
  readonly pass: boolean;
}

export interface ScreenComparison<D extends string> {
  readonly name: string;
  readonly pass: boolean;
  readonly dimensions: readonly DimensionComparison<D>[];
}

export interface BaselineComparisonResult<D extends string> {
  readonly pass: boolean;
  readonly screens: readonly ScreenComparison<D>[];
}

export type ScreenCounts<D extends string> = Readonly<Record<D, number>>;
export type RatchetBaseline<D extends string> = Readonly<Record<string, ScreenCounts<D>>>;

export interface ScreenCountsEntry<D extends string> {
  readonly name: string;
  readonly counts: ScreenCounts<D>;
}

function compareDimension<D extends string>(
  dimension: D,
  baselineCount: number,
  currentCount: number,
): DimensionComparison<D> {
  return { dimension, baselineCount, currentCount, pass: currentCount <= baselineCount };
}

function compareScreen<D extends string>(
  dimensions: readonly D[],
  entry: ScreenCountsEntry<D>,
  baseline: RatchetBaseline<D>,
  emptyCounts: ScreenCounts<D>,
): ScreenComparison<D> {
  const screenBaseline = baseline[entry.name] ?? emptyCounts;
  const dims = dimensions.map((dimension) =>
    compareDimension(dimension, screenBaseline[dimension], entry.counts[dimension]),
  );
  return { name: entry.name, pass: dims.every((d) => d.pass), dimensions: dims };
}

export function compareToBaseline<D extends string>(
  dimensions: readonly D[],
  screens: readonly ScreenCountsEntry<D>[],
  baseline: RatchetBaseline<D>,
  emptyCounts: ScreenCounts<D>,
): BaselineComparisonResult<D> {
  const results = screens.map((entry) => compareScreen(dimensions, entry, baseline, emptyCounts));
  return { pass: results.every((screen) => screen.pass), screens: results };
}

export function assertBaselineNotRaised<D extends string>(
  dimensions: readonly D[],
  oldBaseline: RatchetBaseline<D>,
  newBaseline: RatchetBaseline<D>,
  labelPrefix: string,
  emptyCounts: ScreenCounts<D>,
): void {
  for (const [screenName, newScreen] of Object.entries(newBaseline)) {
    const oldScreen = oldBaseline[screenName] ?? emptyCounts;
    for (const dimension of dimensions) {
      if (newScreen[dimension] > oldScreen[dimension]) {
        throw new Error(
          `${labelPrefix} baseline regression: screen "${screenName}" dimension "${dimension}" ` +
            `raised from ${oldScreen[dimension]} to ${newScreen[dimension]}`,
        );
      }
    }
  }
}

export function formatComparisonDiff<D extends string>(
  comparison: BaselineComparisonResult<D>,
  labelPrefix: string,
): string {
  const regressions = comparison.screens.flatMap((screen) =>
    screen.dimensions
      .filter((dimension) => !dimension.pass)
      .map(
        (dimension) =>
          `${screen.name}.${dimension.dimension}: baseline=${dimension.baselineCount} current=${dimension.currentCount}`,
      ),
  );
  return regressions.length > 0
    ? `${labelPrefix} conformance regressed vs baseline.json: ${regressions.join("; ")}`
    : "no regressions";
}
