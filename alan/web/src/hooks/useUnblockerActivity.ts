import { useMemo } from "react";

import { api } from "../api";
import type { UnblockActivityResponse } from "../api";
import { useDocQuery, useRefetchDoc } from "../api/queries";
import { useChangeRefetch } from "../api/ServerEventsProvider";

export interface UseUnblockerActivityResult {
  snapshot: UnblockActivityResponse | null;
  loaded: boolean;
  error: boolean;
}

const NAMESPACE = "unblocker-activity";
const FLEET_KEY = "default";

const NOT_LOADED: UseUnblockerActivityResult = { snapshot: null, loaded: false, error: false };

function scopeKeyOf(engine: string | null): string {
  return engine === null ? FLEET_KEY : `engine:${engine}`;
}

function resultOf(
  snapshot: UnblockActivityResponse | undefined,
  isError: boolean,
): UseUnblockerActivityResult {
  if (isError) return { snapshot: snapshot ?? null, loaded: true, error: true };
  if (snapshot === undefined) return NOT_LOADED;
  return { snapshot, loaded: true, error: false };
}

export function useUnblockerActivity(engine: string | null): UseUnblockerActivityResult {
  const key = scopeKeyOf(engine);
  const view = useDocQuery(NAMESPACE, key, () => api.getUnblockerActivity(engine));
  const refetch = useRefetchDoc(NAMESPACE, key);

  useChangeRefetch("health", refetch, true);

  return useMemo(() => resultOf(view.data, view.isError), [view.data, view.isError]);
}
