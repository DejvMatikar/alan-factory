import { useCallback, useReducer } from "react";

export type ActionClearOn = "settle" | "rejection";

type BusyEvent = "start" | "finish";

function busyCountReducer(count: number, event: BusyEvent): number {
  return event === "start" ? count + 1 : Math.max(0, count - 1);
}

export interface UseActionResult {
  readonly busy: boolean;
  readonly run: <T>(thunk: () => Promise<T>) => Promise<T>;
  readonly begin: () => void;
  readonly end: () => void;
}

export function useAction(clearOn: ActionClearOn = "settle"): UseActionResult {
  const [count, dispatch] = useReducer(busyCountReducer, 0);
  const busy = count > 0;

  const begin = useCallback((): void => {
    dispatch("start");
  }, []);

  const end = useCallback((): void => {
    dispatch("finish");
  }, []);

  const run = useCallback(
    async <T,>(thunk: () => Promise<T>): Promise<T> => {
      begin();
      try {
        const value = await thunk();
        if (clearOn === "settle") end();
        return value;
      } catch (err) {
        end();
        throw err;
      }
    },
    [clearOn, begin, end],
  );

  return { busy, run, begin, end };
}
