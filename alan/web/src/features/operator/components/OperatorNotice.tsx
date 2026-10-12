import Button from "../../../components/Button";
import {
  PHASE_COPY,
  PHONE_NOTICE,
  UNAVAILABLE_HEADING,
  unavailableLabel,
  updateWarningText,
} from "../operatorCopy";
import type { OperatorPhase } from "../operatorPhase";

const PANEL = "rounded-md border border-border bg-surface-elevated px-4 py-3 text-sm text-text-primary";

export interface OperatorNoticeProps {
  readonly phase: OperatorPhase;
  readonly onStart: () => void;
}

function UnavailableNotice({ reason, detail }: Readonly<{ reason: string; detail: string }>) {
  return (
    <div role="alert" data-testid="operator-unavailable" className={PANEL}>
      <h2 className="text-base font-semibold">{UNAVAILABLE_HEADING}</h2>
      <p data-testid="operator-unavailable-cause" className="mt-1 font-medium">
        {unavailableLabel(reason)}
      </p>
      <p data-testid="operator-unavailable-detail" className="mt-1 text-text-muted">
        {detail}
      </p>
    </div>
  );
}

function StatusNotice({
  kind,
  onStart,
}: Readonly<{ kind: keyof typeof PHASE_COPY; onStart: () => void }>) {
  const startable = kind === "choose" || kind === "ended";
  return (
    <output
      data-testid={`operator-notice-${kind}`}
      className={`${PANEL} flex flex-wrap items-center justify-between gap-3`}
    >
      <span>{PHASE_COPY[kind]}</span>
      {startable && (
        <Button variant="create" data-testid="operator-start" onClick={onStart}>
          Start a session
        </Button>
      )}
    </output>
  );
}

export function UpdateWarning({ warning }: Readonly<{ warning: string }>) {
  return (
    <output data-testid="operator-update-warning" className={`block ${PANEL}`}>
      {updateWarningText(warning)}
    </output>
  );
}

export default function OperatorNotice({ phase, onStart }: Readonly<OperatorNoticeProps>) {
  if (phase.kind === "phone") {
    return (
      <output data-testid="operator-phone-notice" className={`block ${PANEL}`}>
        {PHONE_NOTICE}
      </output>
    );
  }
  if (phase.kind === "unavailable") {
    return <UnavailableNotice reason={phase.reason} detail={phase.detail} />;
  }
  return <StatusNotice kind={phase.kind} onStart={onStart} />;
}
