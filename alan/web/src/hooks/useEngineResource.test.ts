import { act, render, renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type EnginePoolState } from "../api";
import { ServerEventsContext, type ServerEventsValue } from "../api/ServerEventsProvider";
import { useEngineResource, type EngineResourceState } from "./useEngineResource";

const KEY = "acme/widget";
const OTHER_KEY = "acme/other";

type PoolState = EngineResourceState<EnginePoolState>;

interface Held {
  resolve: (value: EnginePoolState) => void;
  reject: (reason: unknown) => void;
}

function pool(freeCount: number): EnginePoolState {
  return {
    slug: KEY,
    size: 4,
    free_count: freeCount,
    leased_count: 0,
    quiescing: false,
    warm: true,
    disk_usage_bytes: 0,
  };
}

function readyPool(
  freeCount: number,
  flags: { refreshing?: boolean; refreshError?: string | null } = {},
): PoolState {
  return {
    status: "ready",
    data: pool(freeCount),
    refreshing: flags.refreshing ?? false,
    refreshError: flags.refreshError ?? null,
  };
}

function holdPoolRequests(): Held[] {
  const held: Held[] = [];
  vi.spyOn(api, "getEnginePool").mockImplementation(
    () =>
      new Promise<EnginePoolState>((resolve, reject) => {
        held.push({ resolve, reject });
      }),
  );
  return held;
}

function neverSettles(): Promise<EnginePoolState> {
  return new Promise<EnginePoolState>(() => undefined);
}

function eventsValue(engines: number, health = 0): ServerEventsValue {
  return { counters: { workspace_list: 0, health, engines }, connected: true };
}

function renderPool(engineKey: string | null = KEY) {
  return renderHook(({ key }: { key: string | null }) => useEngineResource("pool", key), {
    initialProps: { key: engineKey },
  });
}

function refetchNow(refetch: () => void): void {
  act(() => {
    refetch();
  });
}

async function expectState(
  result: { current: { state: PoolState } },
  expected: PoolState,
): Promise<void> {
  await waitFor(() => {
    expect(result.current.state).toEqual(expected);
  });
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("useEngineResource", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("stays loading and never fetches when engineKey is null, even via an explicit refetch()", async () => {
    const spy = vi.spyOn(api, "getEnginePool").mockResolvedValue(pool(1));
    const { result } = renderPool(null);
    refetchNow(result.current.refetch);
    await flush();
    expect(result.current.state).toEqual({ status: "loading" });
    expect(spy).not.toHaveBeenCalled();
  });

  it("reports loading while the first fetch is pending", () => {
    holdPoolRequests();
    const { result } = renderPool();
    expect(result.current.state).toEqual({ status: "loading" });
  });

  it("resolves to ready with the fetched data for the given engine key", async () => {
    const spy = vi.spyOn(api, "getEnginePool").mockResolvedValue(pool(3));
    const { result } = renderPool();
    await expectState(result, readyPool(3));
    expect(spy).toHaveBeenCalledWith(KEY);
  });

  it("surfaces an Error message when the first fetch rejects", async () => {
    vi.spyOn(api, "getEnginePool").mockRejectedValue(new Error("boom"));
    const { result } = renderPool();
    await expectState(result, { status: "error", message: "boom" });
  });

  it("stringifies a non-Error rejection", async () => {
    vi.spyOn(api, "getEnginePool").mockRejectedValue("string-failure");
    const { result } = renderPool();
    await expectState(result, { status: "error", message: "string-failure" });
  });

  it("treats a null payload as ready data rather than loading", async () => {
    vi.spyOn(api, "getEnginePreflight").mockResolvedValue(null);
    const { result } = renderHook(() => useEngineResource("preflight", KEY));
    await waitFor(() => {
      expect(result.current.state).toEqual({
        status: "ready",
        data: null,
        refreshing: false,
        refreshError: null,
      });
    });
  });

  it("keeps the last-good data and marks it refreshing during a refetch, never regressing to loading", async () => {
    const held = holdPoolRequests();
    const { result } = renderPool();
    act(() => {
      held[0]!.resolve(pool(1));
    });
    await expectState(result, readyPool(1));

    refetchNow(result.current.refetch);
    await expectState(result, readyPool(1, { refreshing: true }));

    act(() => {
      held[1]!.resolve(pool(2));
    });
    await expectState(result, readyPool(2));
  });

  it("keeps the prior data and surfaces refreshError with refreshing false when a refetch rejects", async () => {
    vi.spyOn(api, "getEnginePool")
      .mockResolvedValueOnce(pool(1))
      .mockRejectedValueOnce(new Error("refresh boom"));
    const { result } = renderPool();
    await expectState(result, readyPool(1));

    refetchNow(result.current.refetch);
    await expectState(result, readyPool(1, { refreshError: "refresh boom" }));
  });

  it("drops the refresh error the moment the next refetch starts, so refreshing and the error line never co-render", async () => {
    vi.spyOn(api, "getEnginePool")
      .mockResolvedValueOnce(pool(1))
      .mockRejectedValueOnce(new Error("boom"))
      .mockImplementationOnce(neverSettles);
    const { result } = renderPool();
    await expectState(result, readyPool(1));
    refetchNow(result.current.refetch);
    await expectState(result, readyPool(1, { refreshError: "boom" }));

    refetchNow(result.current.refetch);
    await expectState(result, readyPool(1, { refreshing: true }));
  });

  it("clears a prior refreshError once a later refetch succeeds", async () => {
    vi.spyOn(api, "getEnginePool")
      .mockResolvedValueOnce(pool(1))
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(pool(2));
    const { result } = renderPool();
    await expectState(result, readyPool(1));
    refetchNow(result.current.refetch);
    await expectState(result, readyPool(1, { refreshError: "boom" }));

    refetchNow(result.current.refetch);
    await expectState(result, readyPool(2));
  });

  it("returns to loading, not the error arm, when a refetch follows a failed first fetch", async () => {
    vi.spyOn(api, "getEnginePool")
      .mockRejectedValueOnce(new Error("boom"))
      .mockImplementationOnce(neverSettles);
    const { result } = renderPool();
    await expectState(result, { status: "error", message: "boom" });

    refetchNow(result.current.refetch);
    await expectState(result, { status: "loading" });
  });

  it("enters loading on an engineKey change instead of retaining the previous engine's data", async () => {
    const spy = vi
      .spyOn(api, "getEnginePool")
      .mockImplementation((key) => (key === KEY ? Promise.resolve(pool(1)) : neverSettles()));
    const { result, rerender } = renderPool();
    await expectState(result, readyPool(1));

    rerender({ key: OTHER_KEY });
    expect(result.current.state).toEqual({ status: "loading" });
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(OTHER_KEY);
    });
  });

  it("resolves the new engine's data after an engineKey change", async () => {
    const held = holdPoolRequests();
    const { result, rerender } = renderPool();
    act(() => {
      held[0]!.resolve(pool(1));
    });
    await expectState(result, readyPool(1));

    rerender({ key: OTHER_KEY });
    await waitFor(() => {
      expect(held).toHaveLength(2);
    });
    act(() => {
      held[1]!.resolve(pool(2));
    });
    await expectState(result, readyPool(2));
  });

  it("shows a previously loaded engine's cached data as refreshing when switching back to it", async () => {
    const held = holdPoolRequests();
    const { result, rerender } = renderPool();
    act(() => {
      held[0]!.resolve(pool(1));
    });
    await expectState(result, readyPool(1));
    rerender({ key: OTHER_KEY });
    await waitFor(() => {
      expect(held).toHaveLength(2);
    });
    act(() => {
      held[1]!.resolve(pool(2));
    });
    await expectState(result, readyPool(2));

    rerender({ key: KEY });
    await expectState(result, readyPool(1, { refreshing: true }));
    act(() => {
      held[2]!.resolve(pool(3));
    });
    await expectState(result, readyPool(3));
  });

  it("joins a refetch already in flight instead of issuing another request", () => {
    holdPoolRequests();
    const { result } = renderPool();
    act(() => {
      result.current.refetch();
      result.current.refetch();
    });
    expect(api.getEnginePool).toHaveBeenCalledTimes(1);
  });

  it("hands back the same refetch function across re-renders", () => {
    holdPoolRequests();
    const { result, rerender } = renderPool();
    const before = result.current.refetch;
    rerender({ key: KEY });
    expect(result.current.refetch).toBe(before);
  });

  it("re-fetches when the engines server-event counter bumps", async () => {
    const spy = vi.spyOn(api, "getEnginePool").mockResolvedValue(pool(1));
    function Probe() {
      useEngineResource("pool", KEY);
      return null;
    }
    const view = render(
      createElement(ServerEventsContext.Provider, { value: eventsValue(0) }, createElement(Probe)),
    );
    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(1);
    });
    view.rerender(
      createElement(ServerEventsContext.Provider, { value: eventsValue(1) }, createElement(Probe)),
    );
    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(2);
    });
  });

  it("does not re-fetch when another bucket's counter bumps", async () => {
    const spy = vi.spyOn(api, "getEnginePool").mockResolvedValue(pool(1));
    function Probe() {
      useEngineResource("pool", KEY);
      return null;
    }
    const view = render(
      createElement(ServerEventsContext.Provider, { value: eventsValue(0) }, createElement(Probe)),
    );
    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(1);
    });
    view.rerender(
      createElement(
        ServerEventsContext.Provider,
        { value: eventsValue(0, 5) },
        createElement(Probe),
      ),
    );
    await flush();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("never renders refreshing together with a refresh error across a full lifecycle, and ends in loading after an engine change", async () => {
    vi.spyOn(api, "getEnginePool")
      .mockResolvedValueOnce(pool(1))
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(pool(2))
      .mockImplementation(neverSettles);
    const rendered: PoolState[] = [];
    const { result, rerender } = renderHook(
      ({ key }: { key: string }) => {
        const resource = useEngineResource("pool", key);
        rendered.push(resource.state);
        return resource;
      },
      { initialProps: { key: KEY } },
    );
    await expectState(result, readyPool(1));
    refetchNow(result.current.refetch);
    await expectState(result, readyPool(1, { refreshError: "boom" }));
    refetchNow(result.current.refetch);
    await expectState(result, readyPool(2));
    rerender({ key: OTHER_KEY });

    const contradictory = rendered.filter(
      (state) => state.status === "ready" && state.refreshing && state.refreshError !== null,
    );
    expect(contradictory).toEqual([]);
    expect(rendered.at(-1)).toEqual({ status: "loading" });
  });
});
