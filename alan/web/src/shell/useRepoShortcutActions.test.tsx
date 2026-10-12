import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { CommandPaletteContext } from "../components/commandPaletteContext";
import { ShellExploreProvider, useShellExplore } from "./ShellExploreProvider";
import { useRepoShortcutActions } from "./useRepoShortcutActions";

function setup() {
  const openPalette = vi.fn();
  function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return (
      <MemoryRouter initialEntries={["/health"]}>
        <ShellExploreProvider>
          <CommandPaletteContext.Provider value={{ openPalette }}>{children}</CommandPaletteContext.Provider>
        </ShellExploreProvider>
      </MemoryRouter>
    );
  }
  const rendered = renderHook(
    () => ({
      actions: useRepoShortcutActions(),
      location: useLocation(),
      explore: useShellExplore(),
    }),
    { wrapper: Wrapper },
  );
  return { ...rendered, openPalette };
}

describe("useRepoShortcutActions — with a repository", () => {
  it("build navigates to a build conversation scoped to the repository", () => {
    const { result, openPalette } = setup();
    act(() => {
      result.current.actions.build("acme/widget");
    });
    expect(result.current.location.pathname).toBe("/repos/acme/widget/build");
    expect(result.current.location.search).toBe("?engine=acme%2Fwidget");
    expect(openPalette).not.toHaveBeenCalled();
  });

  it("explore opens Explore for the repository", () => {
    const { result, openPalette } = setup();
    act(() => {
      result.current.actions.explore("acme/widget");
    });
    expect(result.current.explore.target).toEqual({ repoKey: "acme/widget" });
    expect(result.current.location.pathname).toBe("/health");
    expect(openPalette).not.toHaveBeenCalled();
  });
});

describe("useRepoShortcutActions — without a repository", () => {
  it("build opens the palette's repository chooser for Build instead of navigating", () => {
    const { result, openPalette } = setup();
    act(() => {
      result.current.actions.build(null);
    });
    expect(openPalette).toHaveBeenCalledExactlyOnceWith({ name: "build-repo" });
    expect(result.current.location.pathname).toBe("/health");
  });

  it("explore opens the palette's consultant chooser for Explore instead of the modal", () => {
    const { result, openPalette } = setup();
    act(() => {
      result.current.actions.explore(null);
    });
    expect(openPalette).toHaveBeenCalledExactlyOnceWith({ name: "explore-consultant" });
    expect(result.current.explore.target).toBeNull();
  });
});

describe("useRepoShortcutActions — identity", () => {
  it("returns the same actions object while nothing it depends on changes", () => {
    const { result, rerender } = setup();
    const first = result.current.actions;
    rerender();
    expect(result.current.actions).toBe(first);
  });
});
