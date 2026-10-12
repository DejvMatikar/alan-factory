import { useSyncExternalStore } from "react";

import { readControlToken, subscribeControlToken } from "../controlToken";

function getSnapshot(): boolean {
  return readControlToken().length > 0;
}

export function useControlTokenPresent(): boolean {
  return useSyncExternalStore(subscribeControlToken, getSnapshot);
}
