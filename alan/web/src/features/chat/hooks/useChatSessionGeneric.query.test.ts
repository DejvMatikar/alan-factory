import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useChatSessionGeneric } from "./useChatSessionGeneric";
import { makeBinding, SID, type TestBrief } from "../../../test/chatSessionHarness";
import { makeChatSession, makeRunSummary } from "../../../test/chatFixtures";

describe("useChatSessionGeneric invalidation path", () => {
  it("refetches exactly once per refreshRunSummary call and keeps the prior summary visible across it", async () => {
    const runSummary = makeRunSummary({ total_cost_usd: 9 });
    const get = vi
      .fn()
      .mockResolvedValueOnce(makeChatSession())
      .mockResolvedValueOnce(makeChatSession({ run_summary: runSummary }));
    const binding = makeBinding({ get });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.runSummary).toEqual(makeRunSummary()));
    expect(get).toHaveBeenCalledTimes(1);

    let refreshDone: Promise<void> | null = null;
    act(() => {
      refreshDone = result.current.refreshRunSummary();
    });
    expect(result.current.runSummary).toEqual(makeRunSummary());
    await act(async () => {
      await refreshDone;
    });
    await waitFor(() => expect(result.current.runSummary).toEqual(runSummary));
    expect(get).toHaveBeenCalledTimes(2);
  });
});

describe("useChatSessionGeneric repo_provision poll", () => {
  it("polls at 1500ms while repo_provision is pending and stops once it resolves", async () => {
    const pending = makeChatSession({ repo_provision: "pending" });
    const ready = makeChatSession({ repo_provision: "ready" });
    const get = vi
      .fn()
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(ready);
    const binding = makeBinding({ get });
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(result.current.repoProvision).toBe("pending");
      expect(get).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(get).toHaveBeenCalledTimes(2);
      expect(result.current.repoProvision).toBe("pending");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(get).toHaveBeenCalledTimes(3);
      expect(result.current.repoProvision).toBe("ready");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(4500);
      });
      expect(get).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not poll before the session's first read resolves", async () => {
    let resolveFirst!: (v: ReturnType<typeof makeChatSession>) => void;
    const get = vi.fn().mockReturnValue(
      new Promise((resolve) => {
        resolveFirst = resolve;
      }),
    );
    const binding = makeBinding({ get });
    vi.useFakeTimers();
    try {
      renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4500);
      });
      expect(get).toHaveBeenCalledTimes(1);
      resolveFirst(makeChatSession({ repo_provision: "pending" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps polling across a failed refetch instead of stopping on the stale pending status", async () => {
    const pending = makeChatSession({ repo_provision: "pending" });
    const get = vi
      .fn()
      .mockResolvedValueOnce(pending)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(pending);
    const binding = makeBinding({ get });
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(get).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(get).toHaveBeenCalledTimes(2);
      expect(result.current.repoProvision).toBe("pending");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(get).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not poll when repo_provision never becomes pending", async () => {
    const binding = makeBinding();
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(result.current.session.kind).toBe("ready");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4500);
      });
      expect(binding.get).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("useChatSessionGeneric summarize vs turn completion", () => {
  it("keeps the brief just set by summarize when a turn-completion refresh lands after it", async () => {
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockResolvedValueOnce(makeChatSession())
        .mockResolvedValueOnce(makeChatSession()),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));

    await act(async () => {
      await result.current.summarize!();
    });
    expect(result.current.brief).toEqual({ content: "summary" });

    await act(async () => {
      await result.current.refreshRunSummary();
    });
    expect(result.current.brief).toEqual({ content: "summary" });
  });
});
