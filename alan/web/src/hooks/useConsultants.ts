import { useMemo } from "react";

import type { ConsultantInfo } from "../api";
import { useConsultantsQuery } from "../api/queries";

const EMPTY_CONSULTANTS: ConsultantInfo[] = [];

export interface UseConsultantsResult {
  consultants: ConsultantInfo[];
  loaded: boolean;
  loadFailed: boolean;
  labelOf: (key: string | null) => string | undefined;
}

export function useConsultants(): UseConsultantsResult {
  const { data, isFetched, isError, errorUpdateCount } = useConsultantsQuery();
  const consultants = data ?? EMPTY_CONSULTANTS;
  const loadFailed = isError || (data === undefined && errorUpdateCount > 0);

  const labelOf = useMemo(() => {
    const byKey = new Map(consultants.map((c) => [c.key, c.label]));
    return (key: string | null): string | undefined =>
      key === null ? undefined : byKey.get(key);
  }, [consultants]);

  return { consultants, loaded: isFetched, loadFailed, labelOf };
}

export function useConsultantLabel(key: string | null): string | undefined {
  const { labelOf } = useConsultants();
  return labelOf(key);
}
