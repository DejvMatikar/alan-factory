import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

import {
  useRefetchDraftRows,
  useScopedDraftRows,
  type UseDraftRowsResult,
} from "../components/ChatDraftRows";
import { FALLBACK_POLL_MS } from "../api/ServerEventsProvider";

export type UseRepoConversationsResult = UseDraftRowsResult;

export function useRepoConversations(engineKey: string): UseRepoConversationsResult {
  const [searchParams] = useSearchParams();
  const activeSessionId = searchParams.get("s");
  const scope = { engineKey };
  const refetch = useRefetchDraftRows(scope);

  useEffect(() => {
    refetch();
  }, [refetch, activeSessionId]);

  return useScopedDraftRows(scope, FALLBACK_POLL_MS);
}
