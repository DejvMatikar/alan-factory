import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { createElement } from "react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { EngineSummary } from "../api";
import {
  DEFAULT_KEY_STATES,
  LISTED_DEFAULT_KEY,
  makeConfiguredEngineSummaryFor,
  makeEngineSummary,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../test/engineFixtures";
import { REPO_SCOPE_STORAGE_KEY, useRepoScope } from "./useRepoScope";

function engines(repos: { owner: string; repo: string }[]): EngineSummary[] {
  return repos.map(({ owner, repo }) => makeEngineSummary({ repo: { owner, repo }, posture: "autonomous" }));
}

function wrapperAt(initial: string) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return createElement(MemoryRouter, { initialEntries: [initial] }, children);
  };
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("useRepoScope — route params win", () => {
  it("returns the key and entry from route params", () => {
    const { result } = renderHook(() => useRepoScope(engines([{ owner: "acme", repo: "widget" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/repos/acme/widget"),
    });
    expect(result.current.key).toBe("acme/widget");
    expect(result.current.entry?.title).toBe("Widget");
  });

  it("params win over a conflicting mirror value", () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "other/repo");
    const { result } = renderHook(() => useRepoScope(engines([{ owner: "acme", repo: "widget" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/repos/acme/widget"),
    });
    expect(result.current.key).toBe("acme/widget");
  });

  it("writes the sessionStorage mirror from the repo route", () => {
    renderHook(() => useRepoScope(engines([{ owner: "acme", repo: "widget" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/repos/acme/widget"),
    });
    expect(sessionStorage.getItem(REPO_SCOPE_STORAGE_KEY)).toBe("acme/widget");
  });

  it("also authorizes from the /config child route", () => {
    const { result } = renderHook(() => useRepoScope(engines([{ owner: "acme", repo: "widget" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/repos/acme/widget/config"),
    });
    expect(result.current.key).toBe("acme/widget");
  });
});

describe("useRepoScope — mirror fallback off the repo route", () => {
  it("is never consulted on the repo route itself, even when it names a different repo", () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/other");
    const { result } = renderHook(
      () =>
        useRepoScope(
          engines([
            { owner: "acme", repo: "widget" },
            { owner: "acme", repo: "other" },
          ]),
          STUB_DEFAULT_ENGINE_KEY,
        ),
      { wrapper: wrapperAt("/repos/acme/widget") },
    );
    expect(result.current.key).toBe("acme/widget");
  });

  it("reads the mirror once off the repo route, when it names a visible entry", () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/widget");
    const { result } = renderHook(() => useRepoScope(engines([{ owner: "acme", repo: "widget" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/health"),
    });
    expect(result.current.key).toBe("acme/widget");
  });

  it("resolves to null when the mirror names a repo absent from the visible engine list", () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/hidden");
    const { result } = renderHook(() => useRepoScope(engines([{ owner: "acme", repo: "widget" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/health"),
    });
    expect(result.current.entry).toBeNull();
    expect(result.current.key).toBeNull();
  });

  it("resolves to null with no mirror and no params", () => {
    const { result } = renderHook(() => useRepoScope(engines([{ owner: "acme", repo: "widget" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/health"),
    });
    expect(result.current.key).toBeNull();
    expect(result.current.entry).toBeNull();
  });

  it("never writes the mirror off the repo route", () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/widget");
    renderHook(() => useRepoScope(engines([{ owner: "acme", repo: "widget" }]), STUB_DEFAULT_ENGINE_KEY), {
      wrapper: wrapperAt("/health"),
    });
    expect(sessionStorage.getItem(REPO_SCOPE_STORAGE_KEY)).toBe("acme/widget");
  });
});

describe("useRepoScope — resolved default", () => {
  it.each(DEFAULT_KEY_STATES)("route params resolve the repository regardless of the default key (%s)", (_state, listed) => {
    const defaultKey = listed;
    const { result } = renderHook(
      () => useRepoScope([makeEngineSummaryFor(defaultKey), makeEngineSummaryFor("acme/widget")], defaultKey),
      { wrapper: wrapperAt("/repos/acme/widget") },
    );
    expect(result.current.key).toBe("acme/widget");
    expect(result.current.entry?.title).toBe("Widget");
    expect(sessionStorage.getItem(REPO_SCOPE_STORAGE_KEY)).toBe("acme/widget");
  });

  it.each(DEFAULT_KEY_STATES)("a mirror naming another visible repository is honoured off the repo route (%s)", (_state, listed) => {
    const defaultKey = listed;
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, "acme/widget");
    const { result } = renderHook(
      () => useRepoScope([makeEngineSummaryFor(defaultKey), makeEngineSummaryFor("acme/widget")], defaultKey),
      { wrapper: wrapperAt("/health") },
    );
    expect(result.current.key).toBe("acme/widget");
  });

  it.each(DEFAULT_KEY_STATES)("a mirror naming the default resolves to the default entry when the switcher lists it (%s)", (_state, listed) => {
    const defaultKey = listed;
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, defaultKey);
    const { result } = renderHook(
      () => useRepoScope([makeEngineSummaryFor(defaultKey), makeEngineSummaryFor("acme/widget")], defaultKey),
      { wrapper: wrapperAt("/health") },
    );
    expect(result.current.key).toBe(defaultKey);
    expect(result.current.entry?.key).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("has no scope from a mirror in an unconfigured-default-only install (no forge target), where the switcher lists nothing (%s)", (_state, listed) => {
    const defaultKey = listed;
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, defaultKey);
    const { result } = renderHook(() => useRepoScope([makeEngineSummaryFor(defaultKey)], defaultKey), {
      wrapper: wrapperAt("/health"),
    });
    expect(result.current).toEqual({ key: null, entry: null });
  });

  it.each(DEFAULT_KEY_STATES)("a mirror naming a lone configured default resolves to it off the repo route (%s)", (_state, listed) => {
    const defaultKey = listed;
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, defaultKey);
    const { result } = renderHook(() => useRepoScope([makeConfiguredEngineSummaryFor(defaultKey)], defaultKey), {
      wrapper: wrapperAt("/health"),
    });
    expect(result.current.key).toBe(defaultKey);
    expect(result.current.entry?.key).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("a lone configured default's dashboard visit scopes a later page in the same router (%s)", (_state, listed) => {
    const defaultKey = listed;
    const visible = [makeConfiguredEngineSummaryFor(defaultKey)];
    const { result } = renderHook(() => ({ scope: useRepoScope(visible, defaultKey), navigate: useNavigate() }), {
      wrapper: wrapperAt(`/repos/${defaultKey}`),
    });
    expect(result.current.scope.key).toBe(defaultKey);
    act(() => {
      void result.current.navigate("/health");
    });
    expect(result.current.scope.key).toBe(defaultKey);
    expect(result.current.scope.entry?.key).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("a fresh tab off the repo route resolves no scope even for a lone configured default (%s)", (_state, listed) => {
    const defaultKey = listed;
    const { result } = renderHook(() => useRepoScope([makeConfiguredEngineSummaryFor(defaultKey)], defaultKey), {
      wrapper: wrapperAt("/health"),
    });
    expect(result.current).toEqual({ key: null, entry: null });
  });

  it("resolves a mirror naming the old built-in value as an ordinary repository beside a different listed default", () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, STUB_DEFAULT_ENGINE_KEY);
    const { result } = renderHook(
      () =>
        useRepoScope(
          [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
          LISTED_DEFAULT_KEY,
        ),
      { wrapper: wrapperAt("/health") },
    );
    expect(result.current.key).toBe(STUB_DEFAULT_ENGINE_KEY);
    expect(result.current.entry?.title).toBe("Alan");
  });

  it("has no scope from a mirror naming the old built-in value when no engine carries it under a different listed default", () => {
    sessionStorage.setItem(REPO_SCOPE_STORAGE_KEY, STUB_DEFAULT_ENGINE_KEY);
    const { result } = renderHook(
      () => useRepoScope([makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor("acme/widget")], LISTED_DEFAULT_KEY),
      { wrapper: wrapperAt("/health") },
    );
    expect(result.current).toEqual({ key: null, entry: null });
  });
});
