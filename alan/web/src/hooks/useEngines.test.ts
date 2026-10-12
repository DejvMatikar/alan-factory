import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement } from "react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChangeKind } from "../api";
import {
  FALLBACK_POLL_MS,
  ServerEventsContext,
  type ServerEventsValue,
} from "../api/ServerEventsProvider";
import { useEnginesListQuery } from "../api/queries";
import { deferredFetch } from "../test/docReadHarness";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../test/engineFixtures";
import { server } from "../test/server";
import {
  useDefaultEngineKey,
  useDefaultEngineState,
  useEngines,
  useEnginesRefresh,
} from "./useEngines";

function enginesResponse(repos: { owner: string; repo: string; hidden?: boolean }[]) {
  return {
    engines: repos.map(({ owner, repo, hidden }) => ({
      repo: { owner, repo },
      posture: "autonomous",
      ...(hidden === undefined ? {} : { hidden }),
    })),
  };
}

type EnginesPayload = ReturnType<typeof enginesResponse> & { default_engine_key?: string | null };
type Step = EnginesPayload | "error" | "held";

const SPROCKET = enginesResponse([{ owner: "sprocket-co", repo: "sprocket" }]);
const ZERO_COUNTERS: Record<ChangeKind, number> = { workspace_list: 0, health: 0, engines: 0 };

function serveEngines(steps: Step[]) {
  const gate = deferredFetch();
  const tally = { calls: 0, release: gate.release };
  server.use(
    http.get("/api/engines", async () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)]!;
      tally.calls += 1;
      if (step === "error") return HttpResponse.error();
      if (step === "held") {
        await gate.settled;
        return HttpResponse.json(SPROCKET);
      }
      return HttpResponse.json(step);
    }),
  );
  return tally;
}

function renderWithBus<T>(callback: () => T, connected = true) {
  let value: ServerEventsValue = { counters: ZERO_COUNTERS, connected };
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(ServerEventsContext.Provider, { value }, children);
  const rendered = renderHook(callback, { wrapper });
  return {
    ...rendered,
    tick: () => {
      value = { ...value, counters: { ...value.counters, engines: value.counters.engines + 1 } };
      rendered.rerender();
    },
  };
}

function useEnginesWithRefresh() {
  useEnginesRefresh();
  return useEngines();
}

function useListedDefault() {
  useEnginesListQuery();
  return useDefaultEngineKey();
}

function useListedDefaultState() {
  useEnginesListQuery();
  return useDefaultEngineState();
}

function useThreeConsumersWithRefresh() {
  useEnginesRefresh();
  return [useEngines(), useEngines(), useEngines()] as const;
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 25);
    });
  });
}

describe("useEngines", () => {
  it("starts with an empty engine list, hasNonDefaultEngine false, and loaded/loadFailed false", () => {
    server.use(http.get("/api/engines", () => HttpResponse.json(enginesResponse([]))));
    const { result } = renderHook(() => useEngines());
    expect(result.current.engines).toEqual([]);
    expect(result.current.hasNonDefaultEngine).toBe(false);
    expect(result.current.loaded).toBe(false);
    expect(result.current.loadFailed).toBe(false);
  });

  it("becomes loaded, loadFailed false, once the initial fetch resolves, even with an empty list", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.json(enginesResponse([]))));
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.loadFailed).toBe(false);
  });

  it("becomes loaded after a rejected fetch too, so a consumer never waits forever, and sets loadFailed so a consumer can tell 'empty' apart from 'failed'", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.error()));
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.engines).toEqual([]);
    expect(result.current.loadFailed).toBe(true);
  });

  it("clears loadFailed once a later fetch succeeds after an earlier failure", async () => {
    serveEngines(["error", SPROCKET]);
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useEnginesWithRefresh());
      await advance(0);
      expect(result.current.loadFailed).toBe(true);
      await advance(FALLBACK_POLL_MS);
      expect(result.current.loadFailed).toBe(false);
      expect(result.current.hasNonDefaultEngine).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stays false and exposes the raw list when only the default engine is registered", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          ...enginesResponse([{ owner: "DejvMatikar", repo: "alan" }]),
          default_engine_key: STUB_DEFAULT_ENGINE_KEY,
        }),
      ),
    );
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.engines).toHaveLength(1);
    });
    expect(result.current.hasNonDefaultEngine).toBe(false);
  });

  it("becomes true and lists both engines once a non-default engine is registered", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json(
          enginesResponse([
            { owner: "DejvMatikar", repo: "alan" },
            { owner: "sprocket-co", repo: "sprocket" },
          ]),
        ),
      ),
    );
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.hasNonDefaultEngine).toBe(true);
    });
    expect(result.current.engines).toHaveLength(2);
  });

  it("tolerates a network error and keeps the last known engine list", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.error()));
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(true);
    });
    expect(result.current.hasNonDefaultEngine).toBe(false);
    expect(result.current.engines).toEqual([]);
  });
});

describe("useEngines — default engine key", () => {
  it("resolves defaultEngineKey to the listed key once the list names one", async () => {
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY);
  });

  it("resolves defaultEngineKey to null when the list omits the field", async () => {
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)] }));
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.defaultEngineKey).toBeNull();
  });

  it("holds a null key until the first response arrives, then switches to the listed key", async () => {
    const gate = deferredFetch();
    server.use(
      http.get("/api/engines", async () => {
        await gate.settled;
        return HttpResponse.json({ engines: [], default_engine_key: LISTED_DEFAULT_KEY });
      }),
    );
    const { result } = renderHook(() => useEngines());
    expect(result.current.loaded).toBe(false);
    expect(result.current.defaultEngineKey).toBeNull();
    act(() => {
      gate.release();
    });
    await waitFor(() => {
      expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY);
    });
    expect(result.current.loaded).toBe(true);
  });

  it("leaves defaultEngineKey null when the load fails", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.error()));
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(true);
    });
    expect(result.current.defaultEngineKey).toBeNull();
  });

  it("counts every registered engine as non-default when the list names no default", async () => {
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)] }));
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.hasNonDefaultEngine).toBe(true);
  });

  it("keeps the last listed key when a later refresh fails", async () => {
    serveEngines([{ ...SPROCKET, default_engine_key: LISTED_DEFAULT_KEY }, "error"]);
    const { result, tick } = renderWithBus(() => useEnginesWithRefresh());
    await waitFor(() => {
      expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY);
    });
    await settle();
    tick();
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(true);
    });
    expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY);
  });

  it("follows a refresh that names a different default", async () => {
    serveEngines([
      { ...SPROCKET, default_engine_key: LISTED_DEFAULT_KEY },
      { ...SPROCKET, default_engine_key: STUB_DEFAULT_ENGINE_KEY },
    ]);
    const { result, tick } = renderWithBus(() => useEnginesWithRefresh());
    await waitFor(() => {
      expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY);
    });
    await settle();
    tick();
    await waitFor(() => {
      expect(result.current.defaultEngineKey).toBe(STUB_DEFAULT_ENGINE_KEY);
    });
  });

  it.each(DEFAULT_KEY_STATES)(
    "counts a lone engine at the built-in key as non-default only when the listed default differs (%s)",
    async (state, defaultKey) => {
      server.use(
        engineListingHandler({
          engines: [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
          defaultKey,
        }),
      );
      const { result } = renderHook(() => useEngines());
      await waitFor(() => {
        expect(result.current.loaded).toBe(true);
      });
      expect(result.current.engines).toHaveLength(1);
      expect(result.current.hasNonDefaultEngine).toBe(state === "other");
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "counts a lone engine at the listed key as default only when the listed default names it (%s)",
    async (state, defaultKey) => {
      server.use(
        engineListingHandler({
          engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
          defaultKey,
        }),
      );
      const { result } = renderHook(() => useEngines());
      await waitFor(() => {
        expect(result.current.loaded).toBe(true);
      });
      expect(result.current.hasNonDefaultEngine).toBe(state !== "other");
    },
  );
});

describe("useDefaultEngineKey", () => {
  it("returns the listed key once the list names one", async () => {
    server.use(engineListingHandler({ engines: [], defaultKey: LISTED_DEFAULT_KEY }));
    const { result } = renderHook(() => useListedDefault());
    await waitFor(() => {
      expect(result.current).toBe(LISTED_DEFAULT_KEY);
    });
  });

  it("returns null when the list omits the field", async () => {
    const tally = serveEngines([enginesResponse([])]);
    const { result } = renderHook(() => useListedDefault());
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    await settle();
    expect(result.current).toBeNull();
  });

  it("returns null when the list names no default explicitly", async () => {
    const tally = serveEngines([{ ...enginesResponse([]), default_engine_key: null }]);
    const { result } = renderHook(() => useListedDefault());
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    await settle();
    expect(result.current).toBeNull();
  });

  it("returns null and issues no request when the list was never loaded", async () => {
    const tally = serveEngines([enginesResponse([])]);
    const { result } = renderHook(() => useDefaultEngineKey());
    await settle();
    expect(result.current).toBeNull();
    expect(tally.calls).toBe(0);
  });
});

describe("useDefaultEngineState", () => {
  it("is unsettled with a null key and issues no request when the list was never loaded", async () => {
    const tally = serveEngines([enginesResponse([])]);
    const { result } = renderHook(() => useDefaultEngineState());
    await settle();
    expect(result.current).toEqual({ key: null, settled: false });
    expect(tally.calls).toBe(0);
  });

  it("settles on the listed key once the list names one", async () => {
    server.use(engineListingHandler({ engines: [], defaultKey: LISTED_DEFAULT_KEY }));
    const { result } = renderHook(() => useListedDefaultState());
    await waitFor(() => {
      expect(result.current).toEqual({ key: LISTED_DEFAULT_KEY, settled: true });
    });
  });

  it("settles on a null key when the list names no default", async () => {
    const tally = serveEngines([enginesResponse([])]);
    const { result } = renderHook(() => useListedDefaultState());
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    await waitFor(() => {
      expect(result.current).toEqual({ key: null, settled: true });
    });
  });

  it("stays unsettled when the load fails", async () => {
    const tally = serveEngines(["error"]);
    const { result } = renderHook(() => useListedDefaultState());
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    await settle();
    expect(result.current).toEqual({ key: null, settled: false });
  });
});

describe("useEngines — identity", () => {
  it("hands out a new visible list on a second successful fetch whose payload is deep-equal but a distinct object", async () => {
    const tally = serveEngines([SPROCKET, SPROCKET]);
    const { result, tick } = renderWithBus(() => useEnginesWithRefresh());
    await waitFor(() => {
      expect(result.current.engines).toHaveLength(1);
    });
    await settle();
    expect(tally.calls).toBe(1);
    const firstEngines = result.current.engines;
    const firstAllEngines = result.current.allEngines;
    tick();
    await waitFor(() => {
      expect(result.current.engines).not.toBe(firstEngines);
    });
    expect(tally.calls).toBe(2);
    expect(result.current.engines).toEqual(firstEngines);
    expect(result.current.allEngines).not.toBe(firstAllEngines);
    expect(result.current.allEngines).toEqual(firstAllEngines);
  });

  it("keeps the same visible list and derived views across a re-render that fetched nothing", async () => {
    const tally = serveEngines([SPROCKET]);
    const { result, rerender } = renderWithBus(() => useEnginesWithRefresh());
    await waitFor(() => {
      expect(result.current.engines).toHaveLength(1);
    });
    await settle();
    const before = result.current;
    rerender();
    expect(result.current.engines).toBe(before.engines);
    expect(result.current.allEngines).toBe(before.allEngines);
    expect(result.current.hiddenEngines).toBe(before.hiddenEngines);
    expect(result.current.hiddenEngineKeys).toBe(before.hiddenEngineKeys);
    expect(tally.calls).toBe(1);
  });

  it("keeps the same empty list across re-renders before the first fetch settles", async () => {
    const tally = serveEngines(["held"]);
    const { result, rerender } = renderHook(() => useEngines());
    const before = result.current;
    rerender();
    rerender();
    expect(result.current.engines).toBe(before.engines);
    expect(result.current.allEngines).toBe(before.allEngines);
    expect(result.current.hiddenEngines).toBe(before.hiddenEngines);
    expect(result.current.hiddenEngineKeys).toBe(before.hiddenEngineKeys);
    expect(result.current.allEngines).toEqual([]);
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    act(() => {
      tally.release();
    });
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
  });

  it("shares one empty list between two consumers that mount before any fetch settles", async () => {
    const tally = serveEngines(["held"]);
    const { result } = renderHook(() => [useEngines(), useEngines()] as const);
    expect(result.current[0].allEngines).toBe(result.current[1].allEngines);
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    act(() => {
      tally.release();
    });
    await waitFor(() => {
      expect(result.current[0].loaded).toBe(true);
    });
    expect(result.current[0].allEngines).toBe(result.current[1].allEngines);
  });
});

describe("useEngines — failure and recovery", () => {
  it("keeps the last known list, settled and failed, when a later fetch rejects", async () => {
    const tally = serveEngines([SPROCKET, "error"]);
    const { result, tick } = renderWithBus(() => useEnginesWithRefresh());
    await waitFor(() => {
      expect(result.current.engines).toHaveLength(1);
    });
    await settle();
    const known = result.current.engines;
    tick();
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(true);
    });
    expect(tally.calls).toBe(2);
    expect(result.current.loaded).toBe(true);
    expect(result.current.engines).toBe(known);
    expect(result.current.hasNonDefaultEngine).toBe(true);
  });

  it("holds loadFailed true through a recovery fetch in flight after a failure that followed a good list, and clears it on success", async () => {
    const tally = serveEngines([SPROCKET, "error", "held"]);
    const { result, tick } = renderWithBus(() => useEnginesWithRefresh());
    await waitFor(() => {
      expect(result.current.engines).toHaveLength(1);
    });
    await settle();
    tick();
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(true);
    });
    tick();
    await waitFor(() => {
      expect(tally.calls).toBe(3);
    });
    expect(result.current.loadFailed).toBe(true);
    expect(result.current.loaded).toBe(true);
    expect(result.current.engines).toHaveLength(1);
    act(() => {
      tally.release();
    });
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(false);
    });
  });

  it("holds loadFailed true through a recovery fetch in flight after a failure with no list ever fetched, and clears it on success", async () => {
    const tally = serveEngines(["error", "held"]);
    const { result, tick } = renderWithBus(() => useEnginesWithRefresh());
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(true);
    });
    tick();
    await waitFor(() => {
      expect(tally.calls).toBe(2);
    });
    expect(result.current.loadFailed).toBe(true);
    expect(result.current.loaded).toBe(true);
    expect(result.current.engines).toEqual([]);
    act(() => {
      tally.release();
    });
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(false);
    });
    expect(result.current.engines).toHaveLength(1);
  });

  it.each([
    { name: "a confirmed-empty list", step: enginesResponse([]), failed: false },
    { name: "a failed fetch", step: "error" as const, failed: true },
  ])("settles $name as loaded with no engines and loadFailed $failed", async ({ step, failed }) => {
    serveEngines([step]);
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect([result.current.loaded, result.current.loadFailed, result.current.engines]).toEqual([
      true,
      failed,
      [],
    ]);
  });

  it("does not retry a failed fetch: one request, settled with no timer advance", async () => {
    const tally = serveEngines(["error"]);
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(true);
    });
    await settle();
    expect(tally.calls).toBe(1);
    expect(result.current.loaded).toBe(true);
  });
});

describe("useEngines — shared cache", () => {
  it("reports loaded on the first render of a remount that finds a cached list, without a second request", async () => {
    const tally = serveEngines([SPROCKET]);
    const first = renderHook(() => useEngines());
    await waitFor(() => {
      expect(first.result.current.loaded).toBe(true);
    });
    first.unmount();
    const second = renderHook(() => useEngines());
    expect(second.result.current.loaded).toBe(true);
    expect(second.result.current.allEngines).toHaveLength(1);
    await settle();
    expect(tally.calls).toBe(1);
  });

  it("reports loadFailed on the first render of a remount that finds a cached list left by a failed refetch", async () => {
    const tally = serveEngines([SPROCKET, "error"]);
    const first = renderWithBus(() => useEnginesWithRefresh());
    await waitFor(() => {
      expect(first.result.current.engines).toHaveLength(1);
    });
    await settle();
    first.tick();
    await waitFor(() => {
      expect(first.result.current.loadFailed).toBe(true);
    });
    first.unmount();
    const second = renderHook(() => useEngines());
    expect(second.result.current.loadFailed).toBe(true);
    expect(second.result.current.loaded).toBe(true);
    expect(second.result.current.allEngines).toHaveLength(1);
    await settle();
    expect(tally.calls).toBe(2);
  });

  it("issues one request for several consumers mounted together", async () => {
    const tally = serveEngines([SPROCKET]);
    const { result } = renderHook(() => useThreeConsumersWithRefresh());
    await waitFor(() => {
      expect(result.current[2].loaded).toBe(true);
    });
    await settle();
    expect(tally.calls).toBe(1);
    expect(result.current[0].allEngines).toBe(result.current[2].allEngines);
  });
});

describe("useEnginesRefresh", () => {
  it("refetches exactly once per engines tick however many consumers are mounted", async () => {
    const tally = serveEngines([SPROCKET]);
    const { result, tick } = renderWithBus(() => useThreeConsumersWithRefresh());
    await waitFor(() => {
      expect(result.current[2].loaded).toBe(true);
    });
    await settle();
    expect(tally.calls).toBe(1);
    tick();
    await waitFor(() => {
      expect(tally.calls).toBe(2);
    });
    await settle();
    expect(tally.calls).toBe(2);
  });

  it("is what subscribes to the bus: consumers alone refetch on no tick and no poll", async () => {
    vi.useFakeTimers();
    const tally = serveEngines([SPROCKET]);
    const { result, tick } = renderWithBus(() => useEngines(), false);
    await advance(0);
    expect(result.current.loaded).toBe(true);
    tick();
    await advance(FALLBACK_POLL_MS * 3);
    expect(tally.calls).toBe(1);
  });

  it("polls once per fallback window while the bus is disconnected, not once per consumer", async () => {
    vi.useFakeTimers();
    const tally = serveEngines([SPROCKET]);
    renderWithBus(() => useThreeConsumersWithRefresh(), false);
    await advance(0);
    expect(tally.calls).toBe(1);
    await advance(FALLBACK_POLL_MS);
    expect(tally.calls).toBe(2);
    await advance(FALLBACK_POLL_MS);
    expect(tally.calls).toBe(3);
  });
});

describe("useEngines — hidden-repo filtering", () => {
  it("engines omits a hidden engine while allEngines keeps it", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          ...enginesResponse([
            { owner: "DejvMatikar", repo: "alan" },
            { owner: "acme", repo: "sprocket", hidden: true },
          ]),
          default_engine_key: STUB_DEFAULT_ENGINE_KEY,
        }),
      ),
    );
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.allEngines).toHaveLength(2);
    });
    expect(result.current.engines.map((e) => e.repo.repo)).toEqual(["alan"]);
  });

  it("hiddenEngines and hiddenCount surface exactly the hidden engines", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json(
          enginesResponse([
            { owner: "DejvMatikar", repo: "alan" },
            { owner: "acme", repo: "sprocket", hidden: true },
            { owner: "acme", repo: "widget", hidden: true },
          ]),
        ),
      ),
    );
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.hiddenCount).toBe(2);
    });
    expect(result.current.hiddenEngines.map((e) => e.repo.repo).sort()).toEqual(["sprocket", "widget"]);
  });

  it("an explicit hidden:false engine counts as visible", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json(
          enginesResponse([{ owner: "acme", repo: "sprocket", hidden: false }]),
        ),
      ),
    );
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.allEngines).toHaveLength(1);
    });
    expect(result.current.engines).toHaveLength(1);
    expect(result.current.hiddenCount).toBe(0);
  });

  it("hiddenEngineKeys surfaces exactly the owner/repo keys of the hidden engines", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json(
          enginesResponse([
            { owner: "DejvMatikar", repo: "alan" },
            { owner: "acme", repo: "sprocket", hidden: true },
            { owner: "acme", repo: "widget", hidden: true },
          ]),
        ),
      ),
    );
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.hiddenCount).toBe(2);
    });
    expect(Array.from(result.current.hiddenEngineKeys).sort()).toEqual([
      "acme/sprocket",
      "acme/widget",
    ]);
    expect(result.current.hiddenEngineKeys.has("DejvMatikar/alan")).toBe(false);
  });

  it("hasNonDefaultEngine ignores a hidden non-default engine — visible-only, like the rest of engines", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          ...enginesResponse([
            { owner: "DejvMatikar", repo: "alan" },
            { owner: "acme", repo: "sprocket", hidden: true },
          ]),
          default_engine_key: STUB_DEFAULT_ENGINE_KEY,
        }),
      ),
    );
    const { result } = renderHook(() => useEngines());
    await waitFor(() => {
      expect(result.current.allEngines).toHaveLength(2);
    });
    expect(result.current.hasNonDefaultEngine).toBe(false);
  });
});

afterEach(() => {
  vi.useRealTimers();
});
