import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { createElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { useRepoSelection } from "./useRepoSelection";

function mockEngines(repos: { owner: string; repo: string }[]): void {
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: repos.map(({ owner, repo }) => ({ repo: { owner, repo }, posture: "autonomous" })),
      }),
    ),
  );
}

function wrapperAt(initial: string) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return createElement(MemoryRouter, { initialEntries: [initial] }, children);
  };
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("useRepoSelection — ?engine= wins when present", () => {
  it("uses the valid ?engine= value regardless of scope", async () => {
    sessionStorage.setItem("repo-scope", "acme/other");
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }, { owner: "acme", repo: "other" }]);
    const { result } = renderHook(() => useRepoSelection(), {
      wrapper: wrapperAt("/build?engine=acme%2Fwidget"),
    });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.selectedEngineKey).toBe("acme/widget");
    expect(result.current.showChooser).toBe(false);
  });

  it("treats an invalid ?engine= value as unset", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const { result } = renderHook(() => useRepoSelection(), {
      wrapper: wrapperAt("/build?engine=not-a-real-repo"),
    });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.selectedEngineKey).toBeUndefined();
  });

  it("treats a present but invalid ?engine= as an explicit unset, not falling back to a stale scope", async () => {
    sessionStorage.setItem("repo-scope", "acme/widget");
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const { result } = renderHook(() => useRepoSelection(), {
      wrapper: wrapperAt("/build?engine=not-a-real-repo"),
    });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.selectedEngineKey).toBeUndefined();
    expect(result.current.showChooser).toBe(true);
  });
});

describe("useRepoSelection — cross-repo scope fallback", () => {
  it("falls back to the known cross-repo scope when ?engine= is absent", async () => {
    sessionStorage.setItem("repo-scope", "acme/widget");
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.selectedEngineKey).toBe("acme/widget"));
    expect(result.current.showChooser).toBe(false);
  });

  it("shows the chooser when no ?engine= and no known scope, with more than one repo connected", async () => {
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.selectedEngineKey).toBeUndefined();
    expect(result.current.showChooser).toBe(true);
  });

  it("never shows the chooser when only the default repo is connected", async () => {
    mockEngines([]);
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.onlyDefaultRepo).toBe(true);
    expect(result.current.showChooser).toBe(false);
  });

  it("does not fall back to scope while resuming (?s= present), leaving the selection unset", async () => {
    sessionStorage.setItem("repo-scope", "acme/widget");
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const { result } = renderHook(() => useRepoSelection(), {
      wrapper: wrapperAt("/build?s=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.selectedEngineKey).toBeUndefined();
    expect(result.current.showChooser).toBe(false);
  });

  it("does not fall back to scope while project-scoped (?project= present), leaving the selection unset", async () => {
    sessionStorage.setItem("repo-scope", "acme/widget");
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const { result } = renderHook(() => useRepoSelection(), {
      wrapper: wrapperAt("/build?project=proj-1"),
    });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.selectedEngineKey).toBeUndefined();
    expect(result.current.showChooser).toBe(false);
  });

  it("ignores a mirrored scope that no longer names a visible repo", async () => {
    sessionStorage.setItem("repo-scope", "acme/removed");
    mockEngines([{ owner: "DejvMatikar", repo: "alan" }, { owner: "acme", repo: "widget" }]);
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.selectedEngineKey).toBeUndefined();
    expect(result.current.showChooser).toBe(true);
  });
});

describe("useRepoSelection — listed default", () => {
  it("reports an install whose only repository is its listed default as default-only with no chooser", async () => {
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.onlyDefaultRepo).toBe(true);
    expect(result.current.showChooser).toBe(false);
    expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY);
    expect(result.current.repos.map((repo) => repo.key)).toEqual([LISTED_DEFAULT_KEY]);
  });

  it("shows no chooser and exposes no default key until the list has loaded", async () => {
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor("acme/widget")],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    expect(result.current.reposLoaded).toBe(false);
    expect(result.current.showChooser).toBe(false);
    expect(result.current.defaultEngineKey).toBeNull();
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY);
    expect(result.current.showChooser).toBe(true);
  });

  it("exposes no default key and registers nothing when the list names none and carries no engine", async () => {
    server.use(engineListingHandler({ engines: [] }));
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.defaultEngineKey).toBeNull();
    expect(result.current.repos).toEqual([]);
    expect(result.current.soleRepoKey).toBeNull();
    expect(result.current.onlyDefaultRepo).toBe(true);
    expect(result.current.showChooser).toBe(false);
  });

  it("resolves a lone registered repository as the sole repository when the list names no default", async () => {
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor("acme/sole")] }));
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.defaultEngineKey).toBeNull();
    expect(result.current.soleRepoKey).toBe("acme/sole");
    expect(result.current.onlyDefaultRepo).toBe(true);
    expect(result.current.showChooser).toBe(false);
  });

  it.each(DEFAULT_KEY_STATES)("resolves the default key and default-only state from the fetched list (%s)", async (_state, listed) => {
    const defaultKey = listed;
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor(defaultKey)], defaultKey: listed }));
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.defaultEngineKey).toBe(defaultKey);
    expect(result.current.onlyDefaultRepo).toBe(true);
    expect(result.current.showChooser).toBe(false);
  });

  it.each(DEFAULT_KEY_STATES)("shows the chooser for the default plus another repository (%s)", async (_state, listed) => {
    const defaultKey = listed;
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(defaultKey), makeEngineSummaryFor("acme/widget")],
        defaultKey: listed,
      }),
    );
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.defaultEngineKey).toBe(defaultKey);
    expect(result.current.onlyDefaultRepo).toBe(false);
    expect(result.current.soleRepoKey).toBeNull();
    expect(result.current.showChooser).toBe(true);
    expect(result.current.repos.map((repo) => repo.key)).toEqual([defaultKey, "acme/widget"]);
  });

  it("treats the old built-in value as an ordinary repository beside a different listed default", async () => {
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.onlyDefaultRepo).toBe(false);
    expect(result.current.showChooser).toBe(true);
    expect(result.current.repos.map((repo) => repo.key)).toEqual([LISTED_DEFAULT_KEY, STUB_DEFAULT_ENGINE_KEY]);
  });

  it("accepts the listed default as a valid ?engine= selection", async () => {
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor("acme/widget")],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    const { result } = renderHook(() => useRepoSelection(), {
      wrapper: wrapperAt(`/build?engine=${encodeURIComponent(LISTED_DEFAULT_KEY)}`),
    });
    await waitFor(() => expect(result.current.reposLoaded).toBe(true));
    expect(result.current.selectedEngineKey).toBe(LISTED_DEFAULT_KEY);
    expect(result.current.showChooser).toBe(false);
  });

  it("selecting the listed default from the chooser sets the selection and dismisses the chooser", async () => {
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor("acme/widget")],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    const { result } = renderHook(() => useRepoSelection(), { wrapper: wrapperAt("/build") });
    await waitFor(() => expect(result.current.showChooser).toBe(true));
    act(() => {
      result.current.selectRepo(LISTED_DEFAULT_KEY);
    });
    expect(result.current.selectedEngineKey).toBe(LISTED_DEFAULT_KEY);
    expect(result.current.showChooser).toBe(false);
  });
});
