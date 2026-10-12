import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ShellExploreContext, ShellExploreProvider, useShellExplore } from "./ShellExploreProvider";

describe("ShellExploreProvider", () => {
  it("the default context value (no provider) is inert", () => {
    const { result } = renderHook(() => useShellExplore());
    expect(result.current.target).toBeNull();
    expect(() => {
      result.current.openExplore("acme/widget");
    }).not.toThrow();
    expect(() => {
      result.current.closeExplore();
    }).not.toThrow();
  });

  it("openExplore sets the target to the given repo key", () => {
    const wrapper = ({ children }: Readonly<{ children: ReactNode }>) => (
      <ShellExploreProvider>{children}</ShellExploreProvider>
    );
    const { result } = renderHook(() => useShellExplore(), { wrapper });
    expect(result.current.target).toBeNull();
    act(() => {
      result.current.openExplore("acme/widget");
    });
    expect(result.current.target).toEqual({ repoKey: "acme/widget" });
  });

  it("closeExplore clears the target back to null", () => {
    const wrapper = ({ children }: Readonly<{ children: ReactNode }>) => (
      <ShellExploreProvider>{children}</ShellExploreProvider>
    );
    const { result } = renderHook(() => useShellExplore(), { wrapper });
    act(() => {
      result.current.openExplore("acme/widget");
    });
    act(() => {
      result.current.closeExplore();
    });
    expect(result.current.target).toBeNull();
  });

  it("exports ShellExploreContext so tests can inject a fixed value", () => {
    const fixed = { target: { repoKey: "acme/widget" }, openExplore: () => undefined, closeExplore: () => undefined };
    const { result } = renderHook(() => useShellExplore(), {
      wrapper: ({ children }: Readonly<{ children: ReactNode }>) => (
        <ShellExploreContext.Provider value={fixed}>{children}</ShellExploreContext.Provider>
      ),
    });
    expect(result.current).toBe(fixed);
  });
});
