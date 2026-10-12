import type { RecoverySnapshot, RecoveryTrendDirection, ResidueItem } from "../../../api";
import { MetricCell } from "./HealthMetricsPanel";
import AggregateChip from "../../../components/AggregateChip";

const _RECOVERY_EXPLANATION =
  "Real, timestamped recovery events seen in the daily digest's current window, and whether that count rose, fell, or held steady against the window before it.";

const _RESIDUE_EXPLANATION =
  "Unclassified anomalies the daily digest's current window carried over — no typed handler covers them yet. Also visible below in the findings table, where each one can be given a disposition.";

const _TREND_GLYPH: Record<RecoveryTrendDirection, string> = { up: "▲", down: "▼", same: "→" };
const _TREND_WORD: Record<RecoveryTrendDirection, string> = {
  up: "up",
  down: "down",
  same: "steady",
};

function _trendSampleText(recovery: RecoverySnapshot): string {
  return `${_TREND_GLYPH[recovery.direction]} ${_TREND_WORD[recovery.direction]} vs previous ${recovery.previous_count}`;
}

const _NOT_MEASURED_YET = "not measured yet";

function _sweepChipState(digestOverdue: boolean | null): { state: "red" | "green" | "unknown"; label: string } {
  if (digestOverdue === null) {
    return { state: "unknown", label: "Unknown" };
  }
  return digestOverdue ? { state: "red", label: "Overdue" } : { state: "green", label: "On time" };
}

export interface DigestSignalSectionProps {
  readonly recovery: RecoverySnapshot | null;
  readonly residue: ResidueItem[] | null;
  readonly digestOverdue: boolean | null;
}

export default function DigestSignalSection({
  recovery,
  residue,
  digestOverdue,
}: Readonly<DigestSignalSectionProps>) {
  const sweepChip = _sweepChipState(digestOverdue);
  return (
    <div data-testid="digest-signal-section" className="mt-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium text-text-primary">Daily digest</h3>
        <AggregateChip state={sweepChip.state} label={sweepChip.label} testIdSuffix="digest-sweep" />
      </div>
      <div className="mt-2 flex flex-wrap items-start gap-8">
        <MetricCell
          label="Recovery events (window)"
          value={recovery === null ? _NOT_MEASURED_YET : String(recovery.current_count)}
          valueTestid="metric-digest-recovery"
          explainer={_RECOVERY_EXPLANATION}
          lowData={recovery === null}
          sampleText={recovery === null ? undefined : _trendSampleText(recovery)}
          sampleTestid="metric-digest-recovery-trend"
        />
        <MetricCell
          label="Unclassified anomalies (window)"
          value={residue === null ? _NOT_MEASURED_YET : String(residue.length)}
          valueTestid="metric-digest-residue"
          explainer={_RESIDUE_EXPLANATION}
          lowData={residue === null}
        />
      </div>
    </div>
  );
}
