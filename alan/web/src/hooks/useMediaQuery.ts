import { useSyncExternalStore } from "react";

export const WIDE_SCREEN_QUERY = "(min-width: 640px)";

function subscribe(query: string, onChange: () => void): () => void {
  const mql = globalThis.matchMedia(query);
  mql.addEventListener("change", onChange);
  return () => {
    mql.removeEventListener("change", onChange);
  };
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => subscribe(query, onChange),
    () => globalThis.matchMedia(query).matches,
  );
}
