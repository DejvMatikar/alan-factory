import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

const DEFAULT_THRESHOLD_PX = 48;

interface ScrollGeometry {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export function isNearBottom(
  scroller: ScrollGeometry,
  threshold = DEFAULT_THRESHOLD_PX,
): boolean {
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= threshold;
}

export interface UseStickToBottomResult {
  anchorRef: RefObject<HTMLDivElement | null>;
  pinned: boolean;
  jumpToLatest: () => void;
}

function readScrollingElement(): ScrollGeometry | null {
  const el = document.scrollingElement;
  if (el == null) return null;
  return { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
}

function scrollToDocumentBottom(anchor: HTMLDivElement | null): void {
  const el = document.scrollingElement;
  if (el == null) {
    anchor?.scrollIntoView({ block: "end" });
    return;
  }
  el.scrollTop = el.scrollHeight;
}

export function useStickToBottom(deps: unknown[]): UseStickToBottomResult {
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);
  const pinnedRef = useRef(true);

  useEffect(() => {
    const onScroll = () => {
      const geometry = readScrollingElement();
      const near = geometry === null ? true : isNearBottom(geometry);
      if (pinnedRef.current === near) return;
      pinnedRef.current = near;
      setPinned(near);
    };
    window.addEventListener("scroll", onScroll, { passive: true });

    const onResize = () => {
      if (!pinnedRef.current) return;
      const geometry = readScrollingElement();
      const near = geometry === null ? true : isNearBottom(geometry);
      if (near) return;
      pinnedRef.current = false;
      setPinned(false);
    };
    const resizeObserver = new ResizeObserver(onResize);
    resizeObserver.observe(document.documentElement);

    return () => {
      window.removeEventListener("scroll", onScroll);
      resizeObserver.disconnect();
    };
  }, []);

  useLayoutEffect(() => {
    if (pinnedRef.current) {
      scrollToDocumentBottom(anchorRef.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const jumpToLatest = useCallback(() => {
    scrollToDocumentBottom(anchorRef.current);
    pinnedRef.current = true;
    setPinned(true);
  }, []);

  return { anchorRef, pinned, jumpToLatest };
}
