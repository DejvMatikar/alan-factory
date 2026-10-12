import { useEffect, useRef, type RefObject } from "react";

export function useAutoScroll(
  deps: unknown[],
  block: ScrollLogicalPosition = "end",
): RefObject<HTMLDivElement | null> {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    /* v8 ignore next */
    if (el === null) return;
    el.scrollIntoView({ block });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}
