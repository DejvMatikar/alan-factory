import type { AutoDispatchState } from "../hooks/autoDispatch";
import AutoDispatchStatus from "./AutoDispatchStatus";
import Button from "../../../components/Button";

export interface AutoDispatchStatusRowProps {
  state: AutoDispatchState;
  onStop: () => void;
  stopTestId?: string;
}

export default function AutoDispatchStatusRow({
  state,
  onStop,
  stopTestId = "auto-dispatch-stop",
}: Readonly<AutoDispatchStatusRowProps>) {
  const canStop = state.running && state.stage !== "dispatching";
  return (
    <div className="flex items-center gap-2">
      <AutoDispatchStatus state={state} />
      {canStop && (
        <Button variant="ghost" onClick={onStop} data-testid={stopTestId}>
          Stop
        </Button>
      )}
    </div>
  );
}
