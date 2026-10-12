import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { createElement } from "react";

import { HealthContext, HealthProvider } from "../api/HealthProvider";
import type { HealthContextValue } from "../api/HealthProvider";
import { server } from "../test/server";
import { useQueuePauseStatus } from "./useQueuePauseStatus";

const HEALTHY = { ok: true as const, queue_paused: false, paused_until: null, health_state: null };

function wrapper({ children }: Readonly<{ children: ReactNode }>) {
  return createElement(HealthProvider, null, children);
}

async function flushAsync(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("useQueuePauseStatus — real timers", () => {
  it("returns not-paused initially when health is healthy", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    const { result } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
    expect(result.current.pausedUntil).toBeNull();
  });

  it("returns paused status when queue_paused is true", async () => {
    const pausedUntil = Date.now() / 1000 + 300;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
    );
    const { result } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(true);
    });
    expect(result.current.pausedUntil).toBe(pausedUntil);
  });

  it("tolerates /api/health errors silently — stays not-paused", async () => {
    server.use(http.get("/api/health", () => HttpResponse.error()));
    const { result } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
    expect(result.current.pausedUntil).toBeNull();
  });
});

describe("useQueuePauseStatus — spend-cap stop", () => {
  const STOP = {
    cause: "Anthropic refused work because a spend limit was reached.",
    fix: "Raise the spend limit, then press Resume.",
    regain_at: null,
  };
  const STOPPED = { ok: true, queue_paused: true, paused_until: 4102444800, health_state: null };

  it("reports the stop the health response carries", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...STOPPED, spend_cap_stop: STOP })),
    );
    const { result } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await waitFor(() => {
      expect(result.current.spendCapStop).toEqual(STOP);
    });
    expect(result.current.queuePaused).toBe(true);
    expect(result.current.pausedUntil).toBe(4102444800);
  });

  it("reports no stop for an ordinary pause", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(STOPPED)));
    const { result } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(true);
    });
    expect(result.current.spendCapStop).toBeNull();
  });

  it("reports no stop when /api/health errors", async () => {
    server.use(http.get("/api/health", () => HttpResponse.error()));
    const { result } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
    expect(result.current.spendCapStop).toBeNull();
  });

  it("reports no stop when the context value omits the field", () => {
    const value: HealthContextValue = {
      queuePaused: true,
      pausedUntil: 4102444800,
      healthState: null,
      blockedCount: 0,
      needsUserCount: 0,
      lastComputedAt: null,
      finisherRate: null,
      qualityMetrics: null,
      sweeper: null,
      refetch: vi.fn(),
    };
    const { result } = renderHook(() => useQueuePauseStatus(), {
      wrapper: ({ children }: Readonly<{ children: ReactNode }>) =>
        createElement(HealthContext.Provider, { value }, children),
    });
    expect(result.current.spendCapStop).toBeNull();
    expect(result.current.queuePaused).toBe(true);
  });
});

describe("useQueuePauseStatus — fake timers (cadence)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls every 5s when not paused", async () => {
    let callCount = 0;
    server.use(
      http.get("/api/health", () => {
        callCount++;
        return HttpResponse.json(HEALTHY);
      }),
    );
    const { unmount } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await act(flushAsync);
    callCount = 0;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    await act(flushAsync);
    expect(callCount).toBe(1);
    unmount();
  });

  it("polls every 1s when paused", async () => {
    const pausedUntil = Date.now() / 1000 + 300;
    let callCount = 0;
    server.use(
      http.get("/api/health", () => {
        callCount++;
        return HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null });
      }),
    );
    const { result, unmount } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await act(flushAsync);
    expect(result.current.queuePaused).toBe(true);
    callCount = 0;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await act(flushAsync);
    expect(callCount).toBe(1);
    unmount();
  });

  it("cleans up interval on unmount", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    const { unmount } = renderHook(() => useQueuePauseStatus(), { wrapper });
    await act(flushAsync);
    unmount();
    expect(() => vi.advanceTimersByTime(10000)).not.toThrow();
  });
});
