import { stageLabel, stopMessage, type AutoDispatchState } from "../hooks/autoDispatch";

export interface AutoDispatchStatusProps {
  state: AutoDispatchState;
}

function textFor(state: AutoDispatchState): string {
  if (state.running && state.stage !== null) return stageLabel(state.stage);
  if (state.stop !== null) return stopMessage(state.stop);
  return "";
}

export default function AutoDispatchStatus({ state }: Readonly<AutoDispatchStatusProps>) {
  return (
    <div
      aria-live="polite"
      data-testid="auto-dispatch-status"
      className="block text-xs text-text-muted"
    >
      {textFor(state)}
    </div>
  );
}
