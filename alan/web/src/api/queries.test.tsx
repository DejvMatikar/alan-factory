import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../test/server";
import {
  useEngineResourceQuery,
  useEnginesListQuery,
  useHealthDigestQuery,
  useHealthQuery,
  useInvalidateEngineResource,
  useInvalidateEnginesList,
  useInvalidateHealthContext,
  useInvalidateHealthDigest,
  useControlTokenStatusQuery,
  useSetControlTokenStatus,
  type EngineResource,
} from "./queries";
import { queryClient } from "./queryClient";

const HEALTHY = { ok: true, queue_paused: false, paused_until: null, health_state: null };
const PAUSED = { ...HEALTHY, queue_paused: true, paused_until: 4102444800 };
const DIGEST = {
  recovery: { current_count: 3, previous_count: 1, direction: "up" },
  residue: [{ fingerprint: "abc123", title: "Unclassified spike" }],
  digest_overdue: true,
};

type Step = Record<string, unknown> | "error";

function serveSteps(path: string, steps: Step[]) {
  const tally = { calls: 0 };
  server.use(
    http.get(path, () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)]!;
      tally.calls += 1;
      return step === "error" ? HttpResponse.error() : HttpResponse.json(step);
    }),
  );
  return tally;
}

function serveHealth(steps: Step[]) {
  return serveSteps("/api/health", steps);
}

function serveDigest(steps: Step[]) {
  return serveSteps("/api/health/digest", steps);
}

function serveBehindGate(
  path: string,
  first: Record<string, unknown>,
  second: Record<string, unknown>,
) {
  const gate: { calls: number; release: () => void } = { calls: 0, release: () => undefined };
  const held = new Promise<void>((resolve) => {
    gate.release = () => {
      resolve();
    };
  });
  server.use(
    http.get(path, async () => {
      gate.calls += 1;
      if (gate.calls === 1) return HttpResponse.json(first);
      await held;
      return HttpResponse.json(second);
    }),
  );
  return gate;
}

function serveHealthBehindGate(first: Record<string, unknown>, second: Record<string, unknown>) {
  return serveBehindGate("/api/health", first, second);
}

function serveHeld(path: string, payload: Record<string, unknown>): () => void {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = () => {
      resolve();
    };
  });
  server.use(
    http.get(path, async () => {
      await held;
      return HttpResponse.json(payload);
    }),
  );
  return release;
}

function useHealthSnapshot() {
  const { data, isSuccess, isError } = useHealthQuery();
  return { data, isSuccess, isError };
}

function useDigestSnapshot(enabled: boolean) {
  const { data, isFetched, isSuccess, isError } = useHealthDigestQuery(enabled);
  return { data, isFetched, isSuccess, isError };
}

function useEveryHealthRead() {
  return {
    health: useHealthSnapshot(),
    digest: useDigestSnapshot(true),
    invalidateHealth: useInvalidateHealthContext(),
    invalidateDigest: useInvalidateHealthDigest(),
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
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

describe("useHealthQuery", () => {
  it("resolves the health payload from GET /api/health", async () => {
    serveHealth([PAUSED]);
    const { result } = renderHook(() => useHealthSnapshot());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(result.current.data).toEqual(PAUSED);
  });

  it("issues one request on mount", async () => {
    const tally = serveHealth([HEALTHY]);
    const { result } = renderHook(() => useHealthSnapshot());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    await settle();
    expect(tally.calls).toBe(1);
  });

  it("reports an error and no data when the first read fails", async () => {
    serveHealth(["error"]);
    const { result } = renderHook(() => useHealthSnapshot());
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    expect(result.current.data).toBeUndefined();
  });

  it("keeps the last good data and reports the error when a later read fails", async () => {
    serveHealth([PAUSED, "error"]);
    const { result } = renderHook(() => ({
      read: useHealthSnapshot(),
      invalidate: useInvalidateHealthContext(),
    }));
    await waitFor(() => {
      expect(result.current.read.isSuccess).toBe(true);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(result.current.read.isError).toBe(true);
    });
    expect(result.current.read.data).toEqual(PAUSED);
  });

  describe("paused-queue poll", () => {
    it("polls every five seconds while the queue is paused", async () => {
      vi.useFakeTimers();
      const tally = serveHealth([PAUSED]);
      renderHook(() => useHealthSnapshot());
      await flush();
      expect(tally.calls).toBe(1);
      await advance(4900);
      expect(tally.calls).toBe(1);
      await advance(100);
      await flush();
      expect(tally.calls).toBe(2);
      await advance(5000);
      await flush();
      expect(tally.calls).toBe(3);
    });

    it("does not poll while the queue is not paused", async () => {
      vi.useFakeTimers();
      const tally = serveHealth([HEALTHY]);
      renderHook(() => useHealthSnapshot());
      await flush();
      await advance(60000);
      await flush();
      expect(tally.calls).toBe(1);
    });

    it("stops polling once a poll reports the queue resumed", async () => {
      vi.useFakeTimers();
      const tally = serveHealth([PAUSED, HEALTHY]);
      renderHook(() => useHealthSnapshot());
      await flush();
      await advance(5000);
      await flush();
      expect(tally.calls).toBe(2);
      await advance(60000);
      await flush();
      expect(tally.calls).toBe(2);
    });

    it("stops polling after a poll fails instead of polling on the stale paused flag", async () => {
      vi.useFakeTimers();
      const tally = serveHealth([PAUSED, "error"]);
      renderHook(() => useHealthSnapshot());
      await flush();
      await advance(5000);
      await flush();
      expect(tally.calls).toBe(2);
      await advance(60000);
      await flush();
      expect(tally.calls).toBe(2);
    });

    it("keeps polling while the tab is hidden", async () => {
      vi.useFakeTimers();
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      const tally = serveHealth([PAUSED]);
      renderHook(() => useHealthSnapshot());
      await flush();
      await advance(5000);
      await flush();
      expect(tally.calls).toBe(2);
    });
  });
});

describe("useHealthDigestQuery", () => {
  it("resolves the digest payload from GET /api/health/digest", async () => {
    serveDigest([DIGEST]);
    const { result } = renderHook(() => useDigestSnapshot(true));
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(result.current.data).toEqual(DIGEST);
  });

  it("issues no request and reports nothing fetched while disabled", async () => {
    const tally = serveDigest([DIGEST]);
    const { result } = renderHook(() => useDigestSnapshot(false));
    await settle();
    expect(tally.calls).toBe(0);
    expect(result.current.isFetched).toBe(false);
  });

  it("fetches once when it is enabled after starting disabled", async () => {
    const tally = serveDigest([DIGEST]);
    const { result, rerender } = renderHook(({ enabled }) => useDigestSnapshot(enabled), {
      initialProps: { enabled: false },
    });
    rerender({ enabled: true });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    await settle();
    expect(tally.calls).toBe(1);
  });

  it("reports fetched with an error and no data when the read fails", async () => {
    serveDigest(["error"]);
    const { result } = renderHook(() => useDigestSnapshot(true));
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    expect(result.current.isFetched).toBe(true);
    expect(result.current.data).toBeUndefined();
  });
});

describe("health invalidators", () => {
  it("refetches only the health context read when the context is invalidated", async () => {
    const health = serveHealth([HEALTHY]);
    const digest = serveDigest([DIGEST]);
    const { result } = renderHook(() => useEveryHealthRead());
    await waitFor(() => {
      expect(result.current.health.isSuccess && result.current.digest.isSuccess).toBe(true);
    });
    act(() => {
      result.current.invalidateHealth();
    });
    await settle();
    expect(health.calls).toBe(2);
    expect(digest.calls).toBe(1);
  });

  it("refetches only the digest read when the digest is invalidated", async () => {
    const health = serveHealth([HEALTHY]);
    const digest = serveDigest([DIGEST]);
    const { result } = renderHook(() => useEveryHealthRead());
    await waitFor(() => {
      expect(result.current.health.isSuccess && result.current.digest.isSuccess).toBe(true);
    });
    act(() => {
      result.current.invalidateDigest();
    });
    await settle();
    expect(health.calls).toBe(1);
    expect(digest.calls).toBe(2);
  });

  it("joins a refetch already in flight instead of cancelling and restarting it", async () => {
    const gate = serveHealthBehindGate(HEALTHY, PAUSED);
    const { result } = renderHook(() => ({
      read: useHealthSnapshot(),
      invalidate: useInvalidateHealthContext(),
    }));
    await waitFor(() => {
      expect(result.current.read.isSuccess).toBe(true);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(gate.calls).toBe(2);
    });
    act(() => {
      result.current.invalidate();
      result.current.invalidate();
    });
    await settle();
    expect(gate.calls).toBe(2);
    act(() => {
      gate.release();
    });
    await waitFor(() => {
      expect(result.current.read.data).toEqual(PAUSED);
    });
  });

  it("returns nothing a caller could await or leave unhandled", async () => {
    serveHealth([HEALTHY]);
    serveDigest([DIGEST]);
    const { result } = renderHook(() => useEveryHealthRead());
    await waitFor(() => {
      expect(result.current.health.isSuccess).toBe(true);
    });
    expect(result.current.invalidateHealth()).toBeUndefined();
    expect(result.current.invalidateDigest()).toBeUndefined();
    await settle();
  });

  it("hands back the same function across re-renders", async () => {
    serveHealth([HEALTHY]);
    serveDigest([DIGEST]);
    const { result, rerender } = renderHook(() => useEveryHealthRead());
    const before = [result.current.invalidateHealth, result.current.invalidateDigest];
    rerender();
    expect(result.current.invalidateHealth).toBe(before[0]);
    expect(result.current.invalidateDigest).toBe(before[1]);
    await settle();
  });
});

const ENGINE_KEY = "acme/widget";
const OTHER_ENGINE_KEY = "acme/other";
const ENGINE_BASE = `/api/engines/${ENGINE_KEY}`;
const POOL_PATH = `${ENGINE_BASE}/pool`;
const OTHER_POOL_PATH = `/api/engines/${OTHER_ENGINE_KEY}/pool`;
const POOL_A = { tag: "a" };
const POOL_B = { tag: "b" };

const ENGINE_RESOURCE_PATHS: Record<EngineResource, string> = {
  engineConfig: ENGINE_BASE,
  pool: POOL_PATH,
  preflight: `${ENGINE_BASE}/preflight`,
  depSweep: `${ENGINE_BASE}/dependency-sweep`,
  onboarding: `${ENGINE_BASE}/onboarding`,
  repoStatus: `${ENGINE_BASE}/status`,
  recurringStatus: `${ENGINE_BASE}/recurring-status`,
};

const RESOURCE_ROWS = Object.entries(ENGINE_RESOURCE_PATHS) as [EngineResource, string][];
const POLLED_ROWS = RESOURCE_ROWS.filter(
  ([resource]) => resource === "repoStatus" || resource === "recurringStatus",
);
const UNPOLLED_ROWS = RESOURCE_ROWS.filter(([row]) => !POLLED_ROWS.some(([polled]) => polled === row));

function renderEngineQuery(resource: EngineResource, engineKey: string | null = ENGINE_KEY) {
  return renderHook(
    ({ key }: { key: string | null }) => useEngineResourceQuery(resource, key),
    { initialProps: { key: engineKey } },
  );
}

describe("useEngineResourceQuery", () => {
  it.each(RESOURCE_ROWS)("reads %s from its own endpoint for the engine key", async (resource, path) => {
    const tally = serveSteps(path, [{ tag: resource }]);
    const { result } = renderEngineQuery(resource);
    await waitFor(() => {
      expect(result.current.data).toEqual({ tag: resource });
    });
    expect(tally.calls).toBe(1);
  });

  it("keys the read by resource and engine key", async () => {
    serveSteps(POOL_PATH, [POOL_A]);
    const { result } = renderEngineQuery("pool");
    await waitFor(() => {
      expect(result.current.data).toEqual(POOL_A);
    });
    expect(queryClient.getQueryCache().getAll().map((query) => query.queryKey)).toEqual([
      ["engine", "pool", ENGINE_KEY],
    ]);
  });

  it("exposes exactly the five projected props", async () => {
    serveSteps(POOL_PATH, [POOL_A]);
    const { result } = renderEngineQuery("pool");
    await waitFor(() => {
      expect(result.current.data).toEqual(POOL_A);
    });
    expect(Object.keys(result.current).sort()).toEqual([
      "data",
      "error",
      "isError",
      "isFetching",
      "isPlaceholderData",
    ]);
  });

  it("issues no request and reports an idle view while the engine key is null", async () => {
    const tally = serveSteps(POOL_PATH, [POOL_A]);
    const { result } = renderEngineQuery("pool", null);
    await settle();
    expect(tally.calls).toBe(0);
    expect(result.current).toStrictEqual({
      data: undefined,
      isError: false,
      error: null,
      isFetching: false,
      isPlaceholderData: false,
    });
  });

  it.each(POLLED_ROWS)("polls %s every sixty seconds, even while the tab is hidden", async (resource, path) => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const tally = serveSteps(path, [{ tag: resource }]);
    renderEngineQuery(resource);
    await flush();
    expect(tally.calls).toBe(1);
    await advance(59_900);
    expect(tally.calls).toBe(1);
    await advance(100);
    await flush();
    expect(tally.calls).toBe(2);
  });

  it.each(UNPOLLED_ROWS)("does not poll %s", async (resource, path) => {
    vi.useFakeTimers();
    const tally = serveSteps(path, [{ tag: resource }]);
    renderEngineQuery(resource);
    await flush();
    await advance(180_000);
    await flush();
    expect(tally.calls).toBe(1);
  });

  it("refetches on remount while serving the cached payload", async () => {
    const tally = serveSteps(POOL_PATH, [POOL_A, POOL_B]);
    const first = renderEngineQuery("pool");
    await waitFor(() => {
      expect(first.result.current.data).toEqual(POOL_A);
    });
    first.unmount();

    const second = renderEngineQuery("pool");
    expect(second.result.current).toMatchObject({ data: POOL_A, isFetching: true });
    await waitFor(() => {
      expect(second.result.current.data).toEqual(POOL_B);
    });
    expect(tally.calls).toBe(2);
  });

  it("flags the previous key's data as placeholder while the next key loads, then clears the flag", async () => {
    serveSteps(POOL_PATH, [POOL_A]);
    const release = serveHeld(OTHER_POOL_PATH, POOL_B);
    const { result, rerender } = renderEngineQuery("pool");
    await waitFor(() => {
      expect(result.current.data).toEqual(POOL_A);
    });

    rerender({ key: OTHER_ENGINE_KEY });
    expect(result.current).toMatchObject({ data: POOL_A, isPlaceholderData: true, isFetching: true });
    act(() => {
      release();
    });
    await waitFor(() => {
      expect(result.current).toMatchObject({ data: POOL_B, isPlaceholderData: false });
    });
  });

  it("re-renders a consumer that reads only isFetching when the fetch settles", async () => {
    serveSteps(POOL_PATH, [POOL_A]);
    const { result } = renderHook(() => useEngineResourceQuery("pool", ENGINE_KEY).isFetching);
    expect(result.current).toBe(true);
    await waitFor(() => {
      expect(result.current).toBe(false);
    });
  });

  it("re-renders a consumer that reads only isPlaceholderData when the key changes and settles", async () => {
    serveSteps(POOL_PATH, [POOL_A]);
    serveSteps(OTHER_POOL_PATH, [POOL_B]);
    const { result, rerender } = renderHook(
      ({ key }: { key: string }) => useEngineResourceQuery("pool", key).isPlaceholderData,
      { initialProps: { key: ENGINE_KEY } },
    );
    await settle();
    rerender({ key: OTHER_ENGINE_KEY });
    expect(result.current).toBe(true);
    await waitFor(() => {
      expect(result.current).toBe(false);
    });
  });

  it("reports an error and no data when the first read fails", async () => {
    serveSteps(POOL_PATH, ["error"]);
    const { result } = renderEngineQuery("pool");
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it("keeps the last good data and reports the error once a later read fails", async () => {
    serveSteps(POOL_PATH, [POOL_A, "error"]);
    const { result } = renderHook(() => ({
      read: useEngineResourceQuery("pool", ENGINE_KEY),
      invalidate: useInvalidateEngineResource("pool", ENGINE_KEY),
    }));
    await waitFor(() => {
      expect(result.current.read.data).toEqual(POOL_A);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(result.current.read).toMatchObject({ data: POOL_A, isError: true, isFetching: false });
    });
  });
});

describe("useInvalidateEngineResource", () => {
  it("refetches only the invalidated resource for that engine key", async () => {
    const pool = serveSteps(POOL_PATH, [POOL_A]);
    const preflight = serveSteps(ENGINE_RESOURCE_PATHS.preflight, [{ tag: "p" }]);
    const otherPool = serveSteps(OTHER_POOL_PATH, [POOL_B]);
    const { result } = renderHook(() => ({
      pool: useEngineResourceQuery("pool", ENGINE_KEY),
      preflight: useEngineResourceQuery("preflight", ENGINE_KEY),
      otherPool: useEngineResourceQuery("pool", OTHER_ENGINE_KEY),
      invalidate: useInvalidateEngineResource("pool", ENGINE_KEY),
    }));
    await waitFor(() => {
      expect(result.current.pool.data).toEqual(POOL_A);
      expect(result.current.preflight.data).toEqual({ tag: "p" });
      expect(result.current.otherPool.data).toEqual(POOL_B);
    });
    act(() => {
      result.current.invalidate();
    });
    await settle();
    expect(pool.calls).toBe(2);
    expect(preflight.calls).toBe(1);
    expect(otherPool.calls).toBe(1);
  });

  it("joins a refetch already in flight instead of cancelling and restarting it", async () => {
    const gate = serveBehindGate(POOL_PATH, POOL_A, POOL_B);
    const { result } = renderHook(() => ({
      read: useEngineResourceQuery("pool", ENGINE_KEY),
      invalidate: useInvalidateEngineResource("pool", ENGINE_KEY),
    }));
    await waitFor(() => {
      expect(result.current.read.data).toEqual(POOL_A);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(gate.calls).toBe(2);
    });
    act(() => {
      result.current.invalidate();
      result.current.invalidate();
    });
    await settle();
    expect(gate.calls).toBe(2);
    act(() => {
      gate.release();
    });
    await waitFor(() => {
      expect(result.current.read.data).toEqual(POOL_B);
    });
  });

  it("returns nothing a caller could await or leave unhandled", async () => {
    serveSteps(POOL_PATH, [POOL_A]);
    const { result } = renderHook(() => ({
      read: useEngineResourceQuery("pool", ENGINE_KEY),
      invalidate: useInvalidateEngineResource("pool", ENGINE_KEY),
    }));
    await waitFor(() => {
      expect(result.current.read.data).toEqual(POOL_A);
    });
    expect(result.current.invalidate()).toBeUndefined();
    await settle();
  });

  it("hands back the same function across re-renders and a new one when the key changes", async () => {
    serveSteps(POOL_PATH, [POOL_A]);
    const { result, rerender } = renderHook(
      ({ key }: { key: string }) => useInvalidateEngineResource("pool", key),
      { initialProps: { key: ENGINE_KEY } },
    );
    const before = result.current;
    rerender({ key: ENGINE_KEY });
    expect(result.current).toBe(before);
    rerender({ key: OTHER_ENGINE_KEY });
    expect(result.current).not.toBe(before);
    await settle();
  });
});

const ENGINES_LIST_PATH = "/api/engines";
const ENGINES_A = { engines: [{ repo: { owner: "acme", repo: "widget" }, posture: "observe" }] };
const ENGINES_B = { engines: [{ repo: { owner: "acme", repo: "other" }, posture: "observe" }] };

function useEnginesListAndInvalidate() {
  return { read: useEnginesListQuery(), invalidate: useInvalidateEnginesList() };
}

describe("useEnginesListQuery", () => {
  it("reads GET /api/engines under the engines list key and its sibling default key only", async () => {
    const tally = serveSteps(ENGINES_LIST_PATH, [ENGINES_A]);
    const { result } = renderHook(() => useEnginesListQuery());
    await waitFor(() => {
      expect(result.current.data).toHaveLength(1);
    });
    expect(tally.calls).toBe(1);
    expect(queryClient.getQueryCache().getAll().map((query) => query.queryKey)).toEqual([
      ["engines", "list"],
      ["engines", "default"],
    ]);
  });

  it("hands back a new data reference on a second successful read of an identical payload", async () => {
    serveSteps(ENGINES_LIST_PATH, [ENGINES_A, ENGINES_A]);
    const { result } = renderHook(() => useEnginesListAndInvalidate());
    await waitFor(() => {
      expect(result.current.read.data).toHaveLength(1);
    });
    const first = result.current.read.data;
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(result.current.read.data).not.toBe(first);
    });
    expect(result.current.read.data).toEqual(first);
  });
});

describe("useInvalidateEnginesList", () => {
  it("refetches only the engines list", async () => {
    const list = serveSteps(ENGINES_LIST_PATH, [ENGINES_A]);
    const pool = serveSteps(POOL_PATH, [POOL_A]);
    const { result } = renderHook(() => ({
      list: useEnginesListQuery(),
      pool: useEngineResourceQuery("pool", ENGINE_KEY),
      invalidate: useInvalidateEnginesList(),
    }));
    await waitFor(() => {
      expect(result.current.list.data).toHaveLength(1);
      expect(result.current.pool.data).toEqual(POOL_A);
    });
    act(() => {
      result.current.invalidate();
    });
    await settle();
    expect(list.calls).toBe(2);
    expect(pool.calls).toBe(1);
  });

  it("joins the mount fetch already in flight instead of cancelling and restarting it", async () => {
    const gate = serveBehindGate(ENGINES_LIST_PATH, ENGINES_A, ENGINES_B);
    const { result } = renderHook(() => useEnginesListAndInvalidate());
    act(() => {
      result.current.invalidate();
      result.current.invalidate();
    });
    await settle();
    expect(gate.calls).toBe(1);
    await waitFor(() => {
      expect(result.current.read.isSuccess).toBe(true);
    });
    expect(gate.calls).toBe(1);
  });

  it("joins a refetch already in flight instead of cancelling and restarting it", async () => {
    const gate = serveBehindGate(ENGINES_LIST_PATH, ENGINES_A, ENGINES_B);
    const { result } = renderHook(() => useEnginesListAndInvalidate());
    await waitFor(() => {
      expect(result.current.read.isSuccess).toBe(true);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(gate.calls).toBe(2);
    });
    act(() => {
      result.current.invalidate();
      result.current.invalidate();
    });
    await settle();
    expect(gate.calls).toBe(2);
    act(() => {
      gate.release();
    });
    await waitFor(() => {
      expect(result.current.read.data?.[0]?.repo.repo).toBe("other");
    });
  });

  it("returns nothing a caller could await or leave unhandled, and the same function across re-renders", async () => {
    serveSteps(ENGINES_LIST_PATH, [ENGINES_A]);
    const { result, rerender } = renderHook(() => useEnginesListAndInvalidate());
    await waitFor(() => {
      expect(result.current.read.isSuccess).toBe(true);
    });
    const before = result.current.invalidate;
    rerender();
    expect(result.current.invalidate).toBe(before);
    expect(result.current.invalidate()).toBeUndefined();
    await settle();
  });
});

describe("useSetControlTokenStatus", () => {
  const SEEDED = { state: "revoked", deadline: null, serverNowMs: null } as const;

  it("writes the status to the control-token status key and to no other", async () => {
    serveSteps("/api/control-token/status", [{ state: "active", deadline: null }]);
    const { result } = renderHook(() => ({
      read: useControlTokenStatusQuery(),
      set: useSetControlTokenStatus(),
    }));
    await waitFor(() => {
      expect(result.current.read.isSuccess).toBe(true);
    });
    act(() => {
      result.current.set(SEEDED);
    });
    expect(queryClient.getQueryData(["control-token", "status"])).toEqual(SEEDED);
    expect(queryClient.getQueryCache().getAll().map((query) => query.queryKey)).toEqual([
      ["control-token", "status"],
    ]);
  });

  it("creates no cache entry when the status query does not exist", () => {
    const { result } = renderHook(() => useSetControlTokenStatus());
    act(() => {
      result.current(SEEDED);
    });
    expect(queryClient.getQueryData(["control-token", "status"])).toBeUndefined();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
  });

  it("hands back the same function across re-renders", () => {
    const { result, rerender } = renderHook(() => useSetControlTokenStatus());
    const before = result.current;
    rerender();
    expect(result.current).toBe(before);
  });
});
