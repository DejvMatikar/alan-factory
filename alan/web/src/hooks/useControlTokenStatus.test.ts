import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type ControlTokenStatus } from "../api";
import { CONTROL_TOKEN_STATUS_POLL_MS } from "../api/queries";
import { queryClient } from "../api/queryClient";
import { server } from "../test/server";
import { useControlTokenStatus } from "./useControlTokenStatus";

const STATUS_PATH = "/api/control-token/status";
const ACTIVE = { state: "active", deadline: "2026-09-14T00:00:00+00:00" };
const EXPIRED = { state: "expired", deadline: "2026-08-01T00:00:00+00:00" };
const ACTIVE_STATUS: ControlTokenStatus = { state: "active", deadline: ACTIVE.deadline, serverNowMs: null };
const EXPIRED_STATUS: ControlTokenStatus = { state: "expired", deadline: EXPIRED.deadline, serverNowMs: null };
const REVOKED_STATUS: ControlTokenStatus = { state: "revoked", deadline: null, serverNowMs: null };

type Step = Record<string, unknown> | "error";

function serveStatus(steps: Step[]) {
  const tally = { calls: 0 };
  server.use(
    http.get(STATUS_PATH, () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)]!;
      tally.calls += 1;
      return step === "error" ? HttpResponse.error() : HttpResponse.json(step);
    }),
  );
  return tally;
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

describe("useControlTokenStatus — real timers", () => {
  it("fetches the status on mount", async () => {
    serveStatus([ACTIVE]);
    const { result } = renderHook(() => useControlTokenStatus());
    await waitFor(() => {
      expect(result.current.status).toEqual(ACTIVE_STATUS);
    });
    expect(result.current.error).toBe(false);
  });

  it("surfaces a fetch failure as error:true without throwing", async () => {
    serveStatus(["error"]);
    const { result } = renderHook(() => useControlTokenStatus());
    await waitFor(() => {
      expect(result.current.error).toBe(true);
    });
    expect(result.current.status).toBeNull();
  });

  it("exposes exactly status, error and applyStatus, with no refetch member", async () => {
    serveStatus([ACTIVE]);
    const { result } = renderHook(() => useControlTokenStatus());
    await waitFor(() => {
      expect(result.current.status).not.toBeNull();
    });
    expect(Object.keys(result.current).sort()).toEqual(["applyStatus", "error", "status"]);
  });

  it("applyStatus lets a caller apply an already-fetched status without a round trip", async () => {
    const tally = serveStatus([ACTIVE]);
    const { result } = renderHook(() => useControlTokenStatus());
    await waitFor(() => {
      expect(result.current.status).not.toBeNull();
    });
    await act(async () => {
      result.current.applyStatus(EXPIRED_STATUS);
      await Promise.resolve();
    });
    expect(result.current.status).toEqual(EXPIRED_STATUS);
    await settle();
    expect(result.current.status).toEqual(EXPIRED_STATUS);
    expect(tally.calls).toBe(1);
  });

  it("applyStatus after unmount neither throws nor issues a request", async () => {
    const tally = serveStatus([ACTIVE]);
    const { result, unmount } = renderHook(() => useControlTokenStatus());
    await waitFor(() => {
      expect(result.current.status).not.toBeNull();
    });
    const { applyStatus } = result.current;
    unmount();
    expect(() => {
      applyStatus(REVOKED_STATUS);
    }).not.toThrow();
    await settle();
    expect(tally.calls).toBe(1);
  });

  it("applyStatus after the unmounted query was collected leaves nothing for a failing remount to show", async () => {
    const tally = serveStatus([ACTIVE, "error"]);
    const first = renderHook(() => useControlTokenStatus());
    await waitFor(() => {
      expect(first.result.current.status).toEqual(ACTIVE_STATUS);
    });
    const { applyStatus } = first.result.current;
    first.unmount();
    await waitFor(() => {
      expect(queryClient.getQueryCache().getAll()).toEqual([]);
    });

    act(() => {
      applyStatus(REVOKED_STATUS);
    });
    expect(queryClient.getQueryCache().getAll()).toEqual([]);

    const second = renderHook(() => useControlTokenStatus());
    await waitFor(() => {
      expect(second.result.current.error).toBe(true);
    });
    expect(second.result.current.status).toBeNull();
    expect(tally.calls).toBe(2);
  });

  it("refetches on remount while serving the cached status, then settles on what the server now says", async () => {
    const tally = serveStatus([ACTIVE, EXPIRED]);
    const first = renderHook(() => useControlTokenStatus());
    await waitFor(() => {
      expect(first.result.current.status).toEqual(ACTIVE_STATUS);
    });
    first.unmount();

    const second = renderHook(() => useControlTokenStatus());
    expect(second.result.current.status).toEqual(ACTIVE_STATUS);
    await waitFor(() => {
      expect(second.result.current.status).toEqual(EXPIRED_STATUS);
    });
    expect(second.result.current.error).toBe(false);
    expect(tally.calls).toBe(2);
  });
});

describe("useControlTokenStatus — fake timers (cadence)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it(`polls exactly once per ${String(CONTROL_TOKEN_STATUS_POLL_MS)}ms`, async () => {
    const tally = serveStatus([ACTIVE]);
    renderHook(() => useControlTokenStatus());
    await flush();
    expect(tally.calls).toBe(1);
    await advance(CONTROL_TOKEN_STATUS_POLL_MS - 100);
    expect(tally.calls).toBe(1);
    await advance(100);
    await flush();
    expect(tally.calls).toBe(2);
    await advance(CONTROL_TOKEN_STATUS_POLL_MS);
    await flush();
    expect(tally.calls).toBe(3);
  });

  it("keeps polling while the tab is hidden", async () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const tally = serveStatus([ACTIVE]);
    renderHook(() => useControlTokenStatus());
    await flush();
    await advance(CONTROL_TOKEN_STATUS_POLL_MS);
    await flush();
    expect(tally.calls).toBe(2);
  });

  it("stops polling on unmount", async () => {
    const tally = serveStatus([ACTIVE]);
    const { unmount } = renderHook(() => useControlTokenStatus());
    await flush();
    expect(tally.calls).toBe(1);
    unmount();
    await advance(CONTROL_TOKEN_STATUS_POLL_MS * 2);
    await flush();
    expect(tally.calls).toBe(1);
  });

  it("keeps the last known status visible and flags the error when a poll fails", async () => {
    const tally = serveStatus([ACTIVE, "error"]);
    const { result } = renderHook(() => useControlTokenStatus());
    await flush();
    expect(result.current.status).toEqual(ACTIVE_STATUS);
    await advance(CONTROL_TOKEN_STATUS_POLL_MS);
    await flush();
    expect(tally.calls).toBe(2);
    expect(result.current.status).toEqual(ACTIVE_STATUS);
    expect(result.current.error).toBe(true);
  });

  it("clears the error flag once a later poll succeeds", async () => {
    serveStatus([ACTIVE, "error", EXPIRED]);
    const { result } = renderHook(() => useControlTokenStatus());
    await flush();
    await advance(CONTROL_TOKEN_STATUS_POLL_MS);
    await flush();
    expect(result.current.error).toBe(true);
    await advance(CONTROL_TOKEN_STATUS_POLL_MS);
    await flush();
    expect(result.current.error).toBe(false);
    expect(result.current.status).toEqual(EXPIRED_STATUS);
  });

  it("applyStatus after a failed poll clears the error flag", async () => {
    serveStatus([ACTIVE, "error"]);
    const { result } = renderHook(() => useControlTokenStatus());
    await flush();
    await advance(CONTROL_TOKEN_STATUS_POLL_MS);
    await flush();
    expect(result.current.error).toBe(true);
    await act(async () => {
      result.current.applyStatus(REVOKED_STATUS);
      await Promise.resolve();
    });
    expect(result.current.error).toBe(false);
    expect(result.current.status).toEqual(REVOKED_STATUS);
  });
});
