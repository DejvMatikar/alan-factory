import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { renderHookWithQuery } from "../test/renderWithQuery";
import { server } from "../test/server";
import {
  CONTROL_TOKEN_STATUS_POLL_MS,
  useControlTokenStatusQuery,
  useEnginesListQuery,
  useHealthQuery,
  useInvalidateHealthContext,
} from "./queries";
import { queryClient } from "./queryClient";

const HEALTHY = { ok: true, queue_paused: false, paused_until: null, health_state: null };
const DEGRADED = {
  ...HEALTHY,
  health_state: { verdict: "degraded", blocked_count: 2, in_flight_count: 0, findings: [] },
};

type Step = Record<string, unknown> | "error";

function serveHealth(steps: Step[]) {
  const tally = { calls: 0 };
  server.use(
    http.get("/api/health", () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)]!;
      tally.calls += 1;
      return step === "error" ? HttpResponse.error() : HttpResponse.json(step);
    }),
  );
  return tally;
}

function serveHealthBehindGate(first: Record<string, unknown>, second: Record<string, unknown>) {
  const gate: { calls: number; release: () => void } = { calls: 0, release: () => undefined };
  const held = new Promise<void>((resolve) => {
    gate.release = () => {
      resolve();
    };
  });
  server.use(
    http.get("/api/health", async () => {
      gate.calls += 1;
      if (gate.calls === 1) return HttpResponse.json(first);
      await held;
      return HttpResponse.json(second);
    }),
  );
  return gate;
}

function useHealthSnapshot() {
  const { data, status, fetchStatus, isFetching, isSuccess, isError, isStale, isPlaceholderData } =
    useHealthQuery();
  return { data, status, fetchStatus, isFetching, isSuccess, isError, isStale, isPlaceholderData };
}

function useHealthAndInvalidate() {
  return { read: useHealthSnapshot(), invalidate: useInvalidateHealthContext() };
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 25);
    });
  });
}

function markInvalidatedWithoutRefetch(): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: ["health", "context"], refetchType: "none" });
}

describe("query client defaults", () => {
  it("does not retry a failed read and surfaces it as an error state rather than a throw", async () => {
    const tally = serveHealth(["error"]);
    const { result } = renderHook(() => useHealthSnapshot());
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    await settle();
    expect(tally.calls).toBe(1);
    expect(result.current.data).toBeUndefined();
  });

  it("treats fetched data as fresh indefinitely", async () => {
    serveHealth([HEALTHY]);
    const { result } = renderHook(() => useHealthSnapshot());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(result.current.isStale).toBe(false);
  });

  it("does not refetch an invalidated read when a consumer remounts", async () => {
    const tally = serveHealth([HEALTHY]);
    const first = renderHook(() => useHealthSnapshot());
    await waitFor(() => {
      expect(first.result.current.isSuccess).toBe(true);
    });
    first.unmount();
    await act(markInvalidatedWithoutRefetch);
    const second = renderHook(() => useHealthSnapshot());
    await settle();
    expect(second.result.current.data).toEqual(HEALTHY);
    expect(second.result.current.fetchStatus).toBe("idle");
    expect(tally.calls).toBe(1);
  });

  it("does not refetch an invalidated read when the window regains focus", async () => {
    const tally = serveHealth([HEALTHY]);
    const { result } = renderHookWithQuery(() => useHealthSnapshot());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    await act(markInvalidatedWithoutRefetch);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
    });
    await settle();
    expect(result.current.fetchStatus).toBe("idle");
    expect(tally.calls).toBe(1);
  });

  it("does not refetch an invalidated read when the network reconnects", async () => {
    const tally = serveHealth([HEALTHY]);
    const { result } = renderHookWithQuery(() => useHealthSnapshot());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    await act(markInvalidatedWithoutRefetch);
    act(() => {
      window.dispatchEvent(new Event("offline"));
      window.dispatchEvent(new Event("online"));
    });
    await settle();
    expect(result.current.fetchStatus).toBe("idle");
    expect(tally.calls).toBe(1);
  });

  it("keeps the previous data readable and the region busy, not pending, across an invalidation", async () => {
    const gate = serveHealthBehindGate(HEALTHY, DEGRADED);
    const { result } = renderHook(() => useHealthAndInvalidate());
    await waitFor(() => {
      expect(result.current.read.isSuccess).toBe(true);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(result.current.read.isFetching).toBe(true);
    });
    expect(result.current.read.data).toEqual(HEALTHY);
    expect(result.current.read.status).toBe("success");
    expect(result.current.read.isPlaceholderData).toBe(false);
    act(() => {
      gate.release();
    });
    await waitFor(() => {
      expect(result.current.read.data).toEqual(DEGRADED);
    });
  });

  it("hands the previous data to a query whose key changes", () => {
    const placeholderData = queryClient.getDefaultOptions().queries?.placeholderData as (
      previous: unknown,
    ) => unknown;
    expect(placeholderData("previous")).toBe("previous");
  });

  it("keeps fetching while the browser reports itself offline", async () => {
    const tally = serveHealth([HEALTHY, DEGRADED]);
    const { result } = renderHookWithQuery(() => useHealthAndInvalidate());
    await waitFor(() => {
      expect(result.current.read.isSuccess).toBe(true);
    });
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    try {
      act(() => {
        result.current.invalidate();
      });
      await waitFor(() => {
        expect(result.current.read.data).toEqual(DEGRADED);
      });
      expect(tally.calls).toBe(2);
    } finally {
      act(() => {
        window.dispatchEvent(new Event("online"));
      });
    }
  });

  it("delivers a settled read to its consumer without waiting on a timer tick", async () => {
    vi.useFakeTimers();
    serveHealth([HEALTHY]);
    const { result } = renderHook(() => useHealthSnapshot());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.data).toEqual(HEALTHY);
  });
});

const INHERITED_ENGINES_LIST_OPTIONS = [
  "retry",
  "networkMode",
  "refetchOnMount",
  "refetchOnWindowFocus",
  "refetchOnReconnect",
  "staleTime",
  "placeholderData",
  "throwOnError",
  "gcTime",
  "refetchInterval",
] as const;

describe("engines list read overrides", () => {
  async function enginesListOptions() {
    server.use(http.get("/api/engines", () => HttpResponse.json({ engines: [] })));
    const { result } = renderHook(() => useEnginesListQuery());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    return queryClient.getQueryCache().find({ queryKey: ["engines", "list"] })!.observers[0]!.options;
  }

  it("inherits retry false, staleTime Infinity and refetchOnMount false from the client defaults", async () => {
    const options = await enginesListOptions();
    expect(options).toMatchObject({ retry: false, staleTime: Infinity, refetchOnMount: false });
  });

  it.each(INHERITED_ENGINES_LIST_OPTIONS)("does not override %s", async (option) => {
    const options = await enginesListOptions();
    expect(options[option]).toBe(queryClient.getDefaultOptions().queries?.[option]);
  });

  it("opts out of structural sharing, the one departure from the client defaults", async () => {
    const options = await enginesListOptions();
    expect(options.structuralSharing).toBe(false);
    expect(queryClient.getDefaultOptions().queries?.structuralSharing).toBeUndefined();
  });

  it("does not retry a failed read", async () => {
    const tally = { calls: 0 };
    server.use(
      http.get("/api/engines", () => {
        tally.calls += 1;
        return HttpResponse.error();
      }),
    );
    const { result } = renderHook(() => useEnginesListQuery());
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    await settle();
    expect(tally.calls).toBe(1);
  });
});

describe("control-token status read overrides", () => {
  it("departs from the client defaults so a dead credential is never served from cache", async () => {
    server.use(
      http.get("/api/control-token/status", () =>
        HttpResponse.json({ state: "active", deadline: null }),
      ),
    );
    const { result } = renderHook(() => useControlTokenStatusQuery());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    const query = queryClient.getQueryCache().find({ queryKey: ["control-token", "status"] });
    expect(query!.observers[0]!.options).toMatchObject({
      staleTime: 0,
      refetchOnMount: "always",
      refetchInterval: CONTROL_TOKEN_STATUS_POLL_MS,
      refetchIntervalInBackground: true,
      gcTime: 0,
    });
  });
});
