import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type EngineSummary, type WorkspaceDetail } from "../api";
import {
  DEFAULT_KEY_STATES,
  LISTED_DEFAULT_KEY,
  makeConfiguredEngineSummaryFor,
  makeEngineSummary,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../test/engineFixtures";
import { makeWorkspaceDetail } from "../test/workspaceFixtures";
import { REPO_SCOPE_STORAGE_KEY, useRepoScope } from "./useRepoScope";

const WORKSPACE_PATHS = [
  ["detail", "/workspaces/t1"],
  ["preparation chat", "/workspaces/t1/prep-chat"],
  ["run full view", "/workspaces/t1/invocations/inv_001"],
] as const;

function engines(repos: { owner: string; repo: string }[]): EngineSummary[] {
  return repos.map(({ owner, repo }) => makeEngineSummary({ repo: { owner, repo }, posture: "autonomous" }));
}

const DEFAULT_AND_WIDGET = engines([
  { owner: "DejvMatikar", repo: "alan" },
  { owner: "acme", repo: "widget" },
]);

function defaultAndWidgetFor(defaultKey: string): EngineSummary[] {
  return [makeEngineSummaryFor(defaultKey), makeEngineSummaryFor("acme/widget")];
}

function wrapperAt(initial: string) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>;
  };
}

function serveWorkspace(engineKey: string | null) {
  return vi
    .spyOn(api, "getWorkspace")
    .mockResolvedValue(makeWorkspaceDetail({ engine_key: engineKey }));
}

function pending<T>() {
  const settlers: { resolve: (value: T) => void; reject: (reason: unknown) => void }[] = [];
  const next = () =>
    new Promise<T>((resolve, reject) => {
      settlers.push({ resolve, reject });
    });
  return { next, settlers };
}

beforeEach(() => {
  sessionStorage.clear();
});

describe.each(WORKSPACE_PATHS)("useRepoScope on the workspace %s page", (_page, path) => {
  it("resolves to the workspace's own repository", async () => {
    serveWorkspace("acme/widget");
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), { wrapper: wrapperAt(path) });
    await waitFor(() => {
      expect(result.current.key).toBe("acme/widget");
    });
    expect(result.current.entry?.title).toBe("Widget");
  });

  it("attributes a workspace with no engine key to the default repository", async () => {
    serveWorkspace(null);
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), { wrapper: wrapperAt(path) });
    await waitFor(() => {
      expect(result.current.key).toBe("DejvMatikar/alan");
    });
    expect(result.current.entry?.title).toBe("Alan");
  });
});

describe("useRepoScope on a workspace page — while unresolved", () => {
  it("has no scope while the workspace is still loading", () => {
    const workspaces = pending<WorkspaceDetail>();
    vi.spyOn(api, "getWorkspace").mockImplementation(() => workspaces.next());
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    expect(result.current).toEqual({ key: null, entry: null });
  });

  it("has no scope when the workspace fails to load", async () => {
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(getWorkspace).toHaveBeenCalledOnce();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.key).toBeNull();
  });

  it("has no scope when the workspace's repository is not among the visible entries", async () => {
    const getWorkspace = serveWorkspace("acme/hidden");
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(getWorkspace).toHaveBeenCalledOnce();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current).toEqual({ key: null, entry: null });
  });

  it("has no scope in an unconfigured-default-only setup (no forge target), where the switcher lists nothing", async () => {
    const getWorkspace = serveWorkspace(null);
    const { result } = renderHook(() => useRepoScope(engines([{ owner: "DejvMatikar", repo: "alan" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(getWorkspace).toHaveBeenCalledOnce();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.key).toBeNull();
  });
});

describe("useRepoScope on a workspace page — the session mirror", () => {
  it("never falls back to the mirror when the workspace's repository cannot be resolved", async () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/widget");
    const getWorkspace = serveWorkspace("acme/hidden");
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(getWorkspace).toHaveBeenCalledOnce();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.key).toBeNull();
  });

  it("prefers the workspace's repository over a conflicting mirror", async () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "DejvMatikar/alan");
    serveWorkspace("acme/widget");
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(result.current.key).toBe("acme/widget");
    });
  });

  it("never writes the mirror from a workspace page", async () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "DejvMatikar/alan");
    serveWorkspace("acme/widget");
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(result.current.key).toBe("acme/widget");
    });
    expect(sessionStorage.getItem(REPO_SCOPE_STORAGE_KEY)).toBe("DejvMatikar/alan");
  });
});

describe("useRepoScope — routes that are not workspace pages", () => {
  it("never requests a workspace off the workspace routes", () => {
    const getWorkspace = serveWorkspace("acme/widget");
    renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), { wrapper: wrapperAt("/health") });
    expect(getWorkspace).not.toHaveBeenCalled();
  });

  it("keeps the repository route's own key, without requesting a workspace", () => {
    const getWorkspace = serveWorkspace("DejvMatikar/alan");
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/repos/acme/widget"),
    });
    expect(result.current.key).toBe("acme/widget");
    expect(getWorkspace).not.toHaveBeenCalled();
  });

  it("does not treat the bare workspaces path as a workspace page", () => {
    const getWorkspace = serveWorkspace("acme/widget");
    const { result } = renderHook(() => useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/workspaces"),
    });
    expect(result.current.key).toBeNull();
    expect(getWorkspace).not.toHaveBeenCalled();
  });
});

describe("useRepoScope — moving between workspaces", () => {
  it("drops the previous workspace's repository while the next one loads", async () => {
    const second = pending<WorkspaceDetail>();
    vi.spyOn(api, "getWorkspace").mockImplementation((taskId) =>
      taskId === "t1" ? Promise.resolve(makeWorkspaceDetail({ engine_key: "acme/widget" })) : second.next(),
    );
    const { result } = renderHook(
      () => ({ scope: useRepoScope(DEFAULT_AND_WIDGET, STUB_DEFAULT_ENGINE_KEY), navigate: useNavigate() }),
      { wrapper: wrapperAt("/workspaces/t1") },
    );
    await waitFor(() => {
      expect(result.current.scope.key).toBe("acme/widget");
    });
    act(() => {
      void result.current.navigate("/workspaces/t2");
    });
    expect(result.current.scope.key).toBeNull();
    await act(async () => {
      second.settlers[0]!.resolve(makeWorkspaceDetail({ engine_key: "DejvMatikar/alan" }));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.scope.key).toBe("DejvMatikar/alan");
    });
  });
});

describe.each(WORKSPACE_PATHS)("useRepoScope on the workspace %s page — resolved default", (_page, path) => {
  it.each(DEFAULT_KEY_STATES)("attributes a workspace with no engine key to the default entry when the switcher lists it (%s)", async (_state, listed) => {
    const defaultKey = listed;
    const visible = defaultAndWidgetFor(defaultKey);
    serveWorkspace(null);
    const { result } = renderHook(() => useRepoScope(visible, defaultKey), { wrapper: wrapperAt(path) });
    await waitFor(() => {
      expect(result.current.key).toBe(defaultKey);
    });
    expect(result.current.entry?.key).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("resolves a workspace naming its own repository to that repository, not the default (%s)", async (_state, listed) => {
    const defaultKey = listed;
    const visible = defaultAndWidgetFor(defaultKey);
    serveWorkspace("acme/widget");
    const { result } = renderHook(() => useRepoScope(visible, defaultKey), { wrapper: wrapperAt(path) });
    await waitFor(() => {
      expect(result.current.key).toBe("acme/widget");
    });
    expect(result.current.entry?.title).toBe("Widget");
  });

  it.each(DEFAULT_KEY_STATES)("resolves a workspace explicitly naming the default to the default entry (%s)", async (_state, listed) => {
    const defaultKey = listed;
    const visible = defaultAndWidgetFor(defaultKey);
    serveWorkspace(defaultKey);
    const { result } = renderHook(() => useRepoScope(visible, defaultKey), { wrapper: wrapperAt(path) });
    await waitFor(() => {
      expect(result.current.key).toBe(defaultKey);
    });
  });
});

describe("useRepoScope on a workspace page — resolved default edge cases", () => {
  it.each(DEFAULT_KEY_STATES)("has no scope for a workspace with no engine key in an unconfigured-default-only install (no forge target) (%s)", async (_state, listed) => {
    const defaultKey = listed;
    const visible = [makeEngineSummaryFor(defaultKey)];
    const getWorkspace = serveWorkspace(null);
    const { result } = renderHook(() => useRepoScope(visible, defaultKey), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(getWorkspace).toHaveBeenCalledOnce();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current).toEqual({ key: null, entry: null });
  });

  it.each(DEFAULT_KEY_STATES)("attributes a workspace with no engine key to a lone configured default (%s)", async (_state, listed) => {
    const defaultKey = listed;
    const visible = [makeConfiguredEngineSummaryFor(defaultKey)];
    serveWorkspace(null);
    const { result } = renderHook(() => useRepoScope(visible, defaultKey), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(result.current.key).toBe(defaultKey);
    });
    expect(result.current.entry?.key).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("resolves a workspace explicitly naming a lone configured default to it (%s)", async (_state, listed) => {
    const defaultKey = listed;
    const visible = [makeConfiguredEngineSummaryFor(defaultKey)];
    serveWorkspace(defaultKey);
    const { result } = renderHook(() => useRepoScope(visible, defaultKey), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(result.current.key).toBe(defaultKey);
    });
    expect(result.current.entry?.key).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("keeps the repository route's own key without requesting a workspace (%s)", (_state, listed) => {
    const defaultKey = listed;
    const visible = defaultAndWidgetFor(defaultKey);
    const getWorkspace = serveWorkspace(null);
    const { result } = renderHook(() => useRepoScope(visible, defaultKey), {
      wrapper: wrapperAt("/repos/acme/widget"),
    });
    expect(result.current.key).toBe("acme/widget");
    expect(getWorkspace).not.toHaveBeenCalled();
  });

  it("attributes a workspace with no engine key to the listed default, not the old built-in value", async () => {
    const visible = [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)];
    serveWorkspace(null);
    const { result } = renderHook(() => useRepoScope(visible, LISTED_DEFAULT_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(result.current.key).toBe(LISTED_DEFAULT_KEY);
    });
  });

  it("resolves a workspace naming the old built-in value as an ordinary repository beside a different listed default", async () => {
    const visible = [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)];
    serveWorkspace(STUB_DEFAULT_ENGINE_KEY);
    const { result } = renderHook(() => useRepoScope(visible, LISTED_DEFAULT_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(result.current.key).toBe(STUB_DEFAULT_ENGINE_KEY);
    });
    expect(result.current.entry?.title).toBe("Alan");
  });

  it("has no scope for a workspace naming the old built-in value when no engine carries it under a different listed default", async () => {
    const visible = defaultAndWidgetFor(LISTED_DEFAULT_KEY);
    const getWorkspace = serveWorkspace(STUB_DEFAULT_ENGINE_KEY);
    const { result } = renderHook(() => useRepoScope(visible, LISTED_DEFAULT_KEY), {
      wrapper: wrapperAt("/workspaces/t1"),
    });
    await waitFor(() => {
      expect(getWorkspace).toHaveBeenCalledOnce();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current).toEqual({ key: null, entry: null });
  });
});
