import { useCallback, useEffect } from "react";
import { useSearchParams } from "react-router-dom";

export const RAIL_COLLAPSE_STORAGE_KEY = "rail-collapse";
const COLLAPSED_VALUE = "collapsed";

export interface UseRailCollapseResult {
  collapsed: boolean;
  toggle: () => void;
}

export function useRailCollapse(): UseRailCollapseResult {
  const [searchParams, setSearchParams] = useSearchParams();
  const paramValue = searchParams.get("rail");
  const collapsed = paramValue === COLLAPSED_VALUE;

  useEffect(() => {
    if (paramValue === COLLAPSED_VALUE) {
      sessionStorage.setItem(RAIL_COLLAPSE_STORAGE_KEY, COLLAPSED_VALUE);
      return;
    }
    if (paramValue !== null) return;
    const mirrored = sessionStorage.getItem(RAIL_COLLAPSE_STORAGE_KEY);
    if (mirrored !== COLLAPSED_VALUE) return;
    const next = new URLSearchParams(searchParams);
    next.set("rail", COLLAPSED_VALUE);
    setSearchParams(next, { replace: true });
  }, [paramValue, searchParams, setSearchParams]);

  const toggle = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    if (collapsed) {
      next.delete("rail");
      sessionStorage.removeItem(RAIL_COLLAPSE_STORAGE_KEY);
    } else {
      next.set("rail", COLLAPSED_VALUE);
      sessionStorage.setItem(RAIL_COLLAPSE_STORAGE_KEY, COLLAPSED_VALUE);
    }
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, collapsed]);

  return { collapsed, toggle };
}
