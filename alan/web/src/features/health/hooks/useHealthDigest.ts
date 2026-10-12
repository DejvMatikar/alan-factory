import { useHealthDigestQuery, useInvalidateHealthDigest } from "../../../api/queries";
import type { RecoverySnapshot, ResidueItem } from "../../../api";
import { useChangeRefetch } from "../../../api/ServerEventsProvider";

export interface UseHealthDigestResult {
  recovery: RecoverySnapshot | null;
  residue: ResidueItem[] | null;
  digestOverdue: boolean | null;
  loaded: boolean;
  error: boolean;
}

export function useHealthDigest(enabled: boolean): UseHealthDigestResult {
  const { data, isFetched, isError } = useHealthDigestQuery(enabled);
  const invalidateDigest = useInvalidateHealthDigest();

  useChangeRefetch("health", invalidateDigest, enabled);

  return {
    recovery: data ? data.recovery : null,
    residue: data ? data.residue : null,
    digestOverdue: data ? data.digest_overdue : null,
    loaded: isFetched,
    error: isError,
  };
}
