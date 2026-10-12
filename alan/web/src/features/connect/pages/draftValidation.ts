import type { EnginePreflightFinding } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";

export type DraftValidationState =
  | { status: "loading" }
  | { status: "ready"; finding: EnginePreflightFinding }
  | { status: "error"; message: string };

// Shared by ConnectStep2Configure.tsx's debounced config-draft-validate
// effect — settles the validate call's outcome into whichever
// DraftValidationState the caller's own effect owns, so the pass/fail
// branching isn't duplicated at each call site.
export function settleDraftValidation(
  promise: Promise<EnginePreflightFinding>,
  isCancelled: () => boolean,
  setState: (state: DraftValidationState) => void,
): Promise<void> {
  return promise.then(
    (finding) => {
      if (!isCancelled()) setState({ status: "ready", finding });
    },
    (err: unknown) => {
      if (!isCancelled()) setState({ status: "error", message: errorMessageOf(err) });
    },
  );
}
