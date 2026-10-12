import {
  useEngineResourceQuery,
  useInvalidateEngineResource,
  type EngineQueryView,
  type EngineResource,
  type EngineResourcePayload,
} from "../api/queries";
import { errorMessageOf } from "../lib/errorMessage";
import { useChangeRefetch } from "../api/ServerEventsProvider";

export type EngineResourceState<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T; refreshing: boolean; refreshError: string | null };

export interface UseEngineResourceResult<T> {
  state: EngineResourceState<T>;
  refetch: () => void;
}

function stateOf<T>(view: EngineQueryView<T>): EngineResourceState<T> {
  if (view.isPlaceholderData) return { status: "loading" };
  if (view.data === undefined) {
    return view.isError
      ? { status: "error", message: errorMessageOf(view.error) }
      : { status: "loading" };
  }
  return {
    status: "ready",
    data: view.data,
    refreshing: view.isFetching,
    refreshError: view.isError && !view.isFetching ? errorMessageOf(view.error) : null,
  };
}

export function useEngineResource<K extends EngineResource>(
  resource: K,
  engineKey: string | null,
): UseEngineResourceResult<EngineResourcePayload<K>> {
  const view = useEngineResourceQuery(resource, engineKey);
  const refetch = useInvalidateEngineResource(resource, engineKey);
  useChangeRefetch("engines", refetch, engineKey !== null);
  return { state: stateOf(view), refetch };
}
