import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WIDE_SCREEN_QUERY, useMediaQuery } from "./useMediaQuery";

class FakeMediaQueryList {
  matches: boolean;
  readonly media: string;
  private listeners: ((event: MediaQueryListEvent) => void)[] = [];

  constructor(media: string, matches: boolean) {
    this.media = media;
    this.matches = matches;
  }

  addEventListener(_type: "change", listener: (event: MediaQueryListEvent) => void): void {
    this.listeners.push(listener);
  }

  removeEventListener(_type: "change", listener: (event: MediaQueryListEvent) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener);
  }

  emit(matches: boolean): void {
    this.matches = matches;
    for (const listener of this.listeners) {
      listener({ matches } as MediaQueryListEvent);
    }
  }
}

function stubMatchMedia(initialMatches: boolean): FakeMediaQueryList {
  const mql = new FakeMediaQueryList(WIDE_SCREEN_QUERY, initialMatches);
  vi.spyOn(window, "matchMedia").mockReturnValue(mql as unknown as MediaQueryList);
  return mql;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useMediaQuery", () => {
  it("returns the initial match state", () => {
    stubMatchMedia(true);
    const { result } = renderHook(() => useMediaQuery(WIDE_SCREEN_QUERY));
    expect(result.current).toBe(true);
  });

  it("returns false when the query does not match", () => {
    stubMatchMedia(false);
    const { result } = renderHook(() => useMediaQuery(WIDE_SCREEN_QUERY));
    expect(result.current).toBe(false);
  });

  it("updates when the media query change event fires", () => {
    const mql = stubMatchMedia(true);
    const { result } = renderHook(() => useMediaQuery(WIDE_SCREEN_QUERY));
    expect(result.current).toBe(true);
    act(() => {
      mql.emit(false);
    });
    expect(result.current).toBe(false);
  });

  it("stops listening after unmount", () => {
    const mql = stubMatchMedia(true);
    const { result, unmount } = renderHook(() => useMediaQuery(WIDE_SCREEN_QUERY));
    unmount();
    act(() => {
      mql.emit(false);
    });
    expect(result.current).toBe(true);
  });
});
