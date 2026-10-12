import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { RAIL_COLLAPSE_STORAGE_KEY, useRailCollapse } from "./useRailCollapse";

function wrapperAt(initial: string) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return createElement(MemoryRouter, { initialEntries: [initial] }, children);
  };
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("useRailCollapse", () => {
  it("starts expanded with no ?rail= param and nothing mirrored", () => {
    const { result } = renderHook(() => useRailCollapse(), { wrapper: wrapperAt("/repos/acme/widget") });
    expect(result.current.collapsed).toBe(false);
  });

  it("reads collapsed straight from ?rail=collapsed", () => {
    const { result } = renderHook(() => useRailCollapse(), {
      wrapper: wrapperAt("/repos/acme/widget?rail=collapsed"),
    });
    expect(result.current.collapsed).toBe(true);
  });

  it("toggle sets ?rail=collapsed and mirrors it to sessionStorage", () => {
    const { result } = renderHook(() => useRailCollapse(), { wrapper: wrapperAt("/repos/acme/widget") });
    act(() => {
      result.current.toggle();
    });
    expect(result.current.collapsed).toBe(true);
    expect(sessionStorage.getItem(RAIL_COLLAPSE_STORAGE_KEY)).toBe("collapsed");
  });

  it("toggle again clears the param and the mirror", () => {
    const { result } = renderHook(() => useRailCollapse(), {
      wrapper: wrapperAt("/repos/acme/widget?rail=collapsed"),
    });
    act(() => {
      result.current.toggle();
    });
    expect(result.current.collapsed).toBe(false);
    expect(sessionStorage.getItem(RAIL_COLLAPSE_STORAGE_KEY)).toBeNull();
  });

  it("re-applies a mirrored collapse onto a fresh navigation that carries no ?rail= of its own", async () => {
    sessionStorage.setItem(RAIL_COLLAPSE_STORAGE_KEY, "collapsed");
    const { result } = renderHook(() => useRailCollapse(), { wrapper: wrapperAt("/repos/acme/widget") });
    await waitFor(() => expect(result.current.collapsed).toBe(true));
  });

  it("a non-'collapsed' mirrored value is ignored, leaving the rail expanded", () => {
    sessionStorage.setItem(RAIL_COLLAPSE_STORAGE_KEY, "garbage");
    const { result } = renderHook(() => useRailCollapse(), { wrapper: wrapperAt("/repos/acme/widget") });
    expect(result.current.collapsed).toBe(false);
  });

  it("a non-'collapsed', non-null ?rail= value is left alone — never re-applies a mirrored collapse over it", () => {
    sessionStorage.setItem(RAIL_COLLAPSE_STORAGE_KEY, "collapsed");
    const { result } = renderHook(() => useRailCollapse(), {
      wrapper: wrapperAt("/repos/acme/widget?rail=expanded"),
    });
    expect(result.current.collapsed).toBe(false);
  });
});
