import { api, type DispatchInstructionsMap } from "../../../api";
import { useDocQuery } from "../../../api/queries";

export interface InstructionReadbacksState {
  readbacks: DispatchInstructionsMap;
  loading: boolean;
}

const EMPTY_READBACKS: DispatchInstructionsMap = Object.freeze({});

export function useInstructionReadbacks(taskId: string | undefined): InstructionReadbacksState {
  const view = useDocQuery("dispatch-readbacks", taskId, (id) => api.getDispatchInstructions(id));
  return {
    readbacks: view.data ?? EMPTY_READBACKS,
    loading: taskId !== undefined && view.data === undefined && !view.isError,
  };
}
