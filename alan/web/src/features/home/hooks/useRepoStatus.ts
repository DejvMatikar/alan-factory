import type { RepoStatus } from "../../../api";
import { useEngineResource, type UseEngineResourceResult } from "../../../hooks/useEngineResource";

export function useRepoStatus(engineKey: string): UseEngineResourceResult<RepoStatus> {
  return useEngineResource("repoStatus", engineKey);
}
