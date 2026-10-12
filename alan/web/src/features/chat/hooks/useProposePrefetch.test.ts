import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { api, type DispatchProposalResponse } from "../../../api";
import { server } from "../../../test/server";
import { useProposePrefetch } from "./useProposePrefetch";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PROPOSE_URL = `/api/build-chat/sessions/${SID}/propose`;
const INTERRUPT_URL = `/api/build-chat/sessions/${SID}/interrupt`;
const EXPLORE_PROPOSE_URL = `/api/explore-chat/sessions/${SID}/propose`;
const EXPLORE_INTERRUPT_URL = `/api/explore-chat/sessions/${SID}/interrupt`;

const DEPS = { propose: api.proposeBuildDispatch, interrupt: api.interruptBuildChatSession };
const EXPLORE_DEPS = { propose: api.proposeExploreDispatch, interrupt: api.interruptExploreChatSession };

const READY: DispatchProposalResponse = {
  status: "ready",
  proposal: {
    team: "implementation_plan",
    rationale: "r",
    instructions: null,
    scope_audit: [],
    counter_case: { against: "a", cheapest_80: "b", cost_estimate: "c" },
  },
  collision: { state: "green", sentence: "ok", match: null },
};

function neverSettles(): Promise<never> {
  return new Promise<never>(() => undefined);
}

describe("useProposePrefetch", () => {
  it("issue is a no-op when sessionId is null", () => {
    let calls = 0;
    server.use(http.post(PROPOSE_URL, () => { calls += 1; return HttpResponse.json(READY); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(null, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    expect(calls).toBe(0);
  });

  it("issue is a defensive no-op when a prefetch is already pending", async () => {
    let calls = 0;
    server.use(http.post(PROPOSE_URL, () => { calls += 1; return neverSettles(); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief-a"); });
    await waitFor(() => expect(calls).toBe(1));
    act(() => { result.current.issue("brief-b"); });
    expect(calls).toBe(1);
  });

  it("issue's settled result is later consumed as immediate, with no extra propose call", async () => {
    let calls = 0;
    server.use(http.post(PROPOSE_URL, () => { calls += 1; return HttpResponse.json(READY); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    const outcome = result.current.consume(SID, "brief");
    expect(outcome).toEqual({ immediate: READY });
    expect(calls).toBe(1);
  });

  it("consume falls through to a fresh propose call when nothing was prefetched", async () => {
    let calls = 0;
    server.use(http.post(PROPOSE_URL, () => { calls += 1; return HttpResponse.json(READY); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    const outcome = result.current.consume(SID, "brief");
    if (!("pending" in outcome)) throw new Error("expected pending");
    await expect(outcome.pending).resolves.toEqual(READY);
    expect(calls).toBe(1);
  });

  it("consume returns the same in-flight promise when the prefetch matches but hasn't resolved yet", async () => {
    let calls = 0;
    let resolvePropose!: (v: DispatchProposalResponse) => void;
    server.use(
      http.post(PROPOSE_URL, () => {
        calls += 1;
        return new Promise((resolve) => {
          resolvePropose = (v) => resolve(HttpResponse.json(v));
        });
      }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(calls).toBe(1));
    const outcome = result.current.consume(SID, "brief");
    if (!("pending" in outcome)) throw new Error("expected pending");
    resolvePropose(READY);
    await expect(outcome.pending).resolves.toEqual(READY);
    expect(calls).toBe(1);
  });

  it("consume discards a mismatched (edited) prefetch, interrupts it once it actually settles, then runs a fresh propose", async () => {
    let proposeCalls = 0;
    let interruptCalls = 0;
    let resolveStalePropose!: () => void;
    let signalSettled!: () => void;
    const settled = new Promise<void>((resolve) => { signalSettled = resolve; });
    server.use(
      http.post(PROPOSE_URL, () => {
        proposeCalls += 1;
        if (proposeCalls === 1) {
          return new Promise((resolve) => {
            resolveStalePropose = () =>
              resolve(HttpResponse.json({ detail: "terminated" }, { status: 502 }));
          });
        }
        return HttpResponse.json(READY);
      }),
      http.post(INTERRUPT_URL, async () => {
        interruptCalls += 1;
        if (interruptCalls === 1) {
          resolveStalePropose();
          await settled;
        }
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const refresh = vi.fn().mockImplementation(() => {
      signalSettled();
      return Promise.resolve();
    });
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief-a"); });
    await waitFor(() => expect(proposeCalls).toBe(1));
    const outcome = result.current.consume(SID, "brief-b");
    if (!("pending" in outcome)) throw new Error("expected pending");
    await expect(outcome.pending).resolves.toEqual(READY);
    expect(proposeCalls).toBe(2);
    expect(interruptCalls).toBe(1);
  });

  it("consume discards an already-errored prefetch without calling interrupt, then runs a fresh propose", async () => {
    let proposeCalls = 0;
    let interruptCalls = 0;
    server.use(
      http.post(PROPOSE_URL, () => {
        proposeCalls += 1;
        if (proposeCalls === 1) return HttpResponse.json({ detail: "boom" }, { status: 502 });
        return HttpResponse.json(READY);
      }),
      http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    const outcome = result.current.consume(SID, "brief");
    if (!("pending" in outcome)) throw new Error("expected pending");
    await expect(outcome.pending).resolves.toEqual(READY);
    expect(proposeCalls).toBe(2);
    expect(interruptCalls).toBe(0);
  });

  it("consume's fresh call rejects (with no retry) when the propose request fails outright with a non-409", async () => {
    let calls = 0;
    server.use(
      http.post(PROPOSE_URL, () => { calls += 1; return HttpResponse.json({ detail: "boom" }, { status: 502 }); }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    const outcome = result.current.consume(SID, "brief");
    if (!("pending" in outcome)) throw new Error("expected pending");
    await expect(outcome.pending).rejects.toThrow("502");
    expect(calls).toBe(1);
  });

  it("a fresh propose retries once on 409 then succeeds", async () => {
    let calls = 0;
    server.use(
      http.post(PROPOSE_URL, () => {
        calls += 1;
        if (calls === 1) return HttpResponse.json({ detail: "locked" }, { status: 409 });
        return HttpResponse.json(READY);
      }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    const outcome = result.current.consume(SID, "brief");
    if (!("pending" in outcome)) throw new Error("expected pending");
    await expect(outcome.pending).resolves.toEqual(READY);
    expect(calls).toBe(2);
  });

  it("a fresh propose rethrows 409 after exhausting its retry budget", async () => {
    let calls = 0;
    server.use(
      http.post(PROPOSE_URL, () => { calls += 1; return HttpResponse.json({ detail: "locked" }, { status: 409 }); }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    const outcome = result.current.consume(SID, "brief");
    if (!("pending" in outcome)) throw new Error("expected pending");
    await expect(outcome.pending).rejects.toThrow("409");
    expect(calls).toBe(4);
  }, 10000);

  it("cancelPrefetchLock exhausts every retry when the discarded prefetch never settles, then still runs the fresh propose", async () => {
    let proposeCalls = 0;
    let interruptCalls = 0;
    server.use(
      http.post(PROPOSE_URL, () => {
        proposeCalls += 1;
        if (proposeCalls === 1) return neverSettles();
        return HttpResponse.json(READY);
      }),
      http.post(INTERRUPT_URL, () => { interruptCalls += 1; return HttpResponse.json({ detail: "boom" }, { status: 500 }); }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief-a"); });
    const outcome = result.current.consume(SID, "brief-b");
    if (!("pending" in outcome)) throw new Error("expected pending");
    await expect(outcome.pending).resolves.toEqual(READY);
    expect(interruptCalls).toBe(4);
    expect(proposeCalls).toBe(2);
  }, 10000);

  it("cancelBeforeLockingAction runs the action directly when no prefetch is pending", async () => {
    let interruptCalls = 0;
    server.use(http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    const action = vi.fn().mockResolvedValue("done");
    await expect(result.current.cancelBeforeLockingAction(action)).resolves.toBe("done");
    expect(action).toHaveBeenCalledTimes(1);
    expect(interruptCalls).toBe(0);
  });

  it("cancelBeforeLockingAction cancels an outstanding prefetch before running the action", async () => {
    let interruptCalls = 0;
    server.use(
      http.post(PROPOSE_URL, () => neverSettles()),
      http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    const action = vi.fn().mockResolvedValue("sent");
    await expect(result.current.cancelBeforeLockingAction(action)).resolves.toBe("sent");
    expect(interruptCalls).toBe(4);
    expect(action).toHaveBeenCalledTimes(1);
  }, 10000);

  it("cancelBeforeLockingAction's retry treats a non-Error rejection as non-409 and rethrows immediately", async () => {
    server.use(http.post(PROPOSE_URL, () => HttpResponse.json({ detail: "boom" }, { status: 502 })));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    let actionCalls = 0;
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    const action = () => { actionCalls += 1; return Promise.reject("plain string rejection"); };
    await expect(result.current.cancelBeforeLockingAction(action)).rejects.toBe(
      "plain string rejection",
    );
    expect(actionCalls).toBe(1);
  });

  it("cancelBeforeLockingAction runs the action directly if sessionId has gone null since the prefetch was issued", async () => {
    server.use(http.post(PROPOSE_URL, () => neverSettles()));
    let interruptCalls = 0;
    server.use(http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const initialProps: { sid: string | null } = { sid: SID };
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useProposePrefetch(sid, refresh, DEPS),
      { initialProps },
    );
    act(() => { result.current.issue("brief"); });
    rerender({ sid: null });
    const action = vi.fn().mockResolvedValue("ok");
    await expect(result.current.cancelBeforeLockingAction(action)).resolves.toBe("ok");
    expect(interruptCalls).toBe(0);
    expect(action).toHaveBeenCalledTimes(1);
  });

  it("discardStale/reset are no-ops when there's no prefetch to discard", () => {
    let interruptCalls = 0;
    server.use(http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.discardStale(); });
    act(() => { result.current.reset(); });
    expect(interruptCalls).toBe(0);
  });

  it("discardStale fires a best-effort interrupt and clears the pending prefetch", async () => {
    server.use(http.post(PROPOSE_URL, () => neverSettles()));
    let interruptCalls = 0;
    server.use(http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    act(() => { result.current.discardStale(); });
    await waitFor(() => expect(interruptCalls).toBe(1));
    act(() => { result.current.discardStale(); });
    expect(interruptCalls).toBe(1);
  });

  it("reset fires a best-effort interrupt and clears the pending prefetch", async () => {
    server.use(http.post(PROPOSE_URL, () => neverSettles()));
    let interruptCalls = 0;
    server.use(http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    act(() => { result.current.reset(); });
    await waitFor(() => expect(interruptCalls).toBe(1));
  });

  it("reset's best-effort interrupt swallows an outright interrupt failure", async () => {
    server.use(http.post(PROPOSE_URL, () => neverSettles()));
    let interruptCalls = 0;
    server.use(
      http.post(INTERRUPT_URL, () => {
        interruptCalls += 1;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    act(() => { result.current.reset(); });
    await waitFor(() => expect(interruptCalls).toBe(1));
  });

  it("discardStale/reset skip the interrupt call if sessionId has gone null since the prefetch was issued", () => {
    server.use(http.post(PROPOSE_URL, () => neverSettles()));
    let interruptCalls = 0;
    server.use(http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const initialProps: { sid: string | null } = { sid: SID };
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useProposePrefetch(sid, refresh, DEPS),
      { initialProps },
    );
    act(() => { result.current.issue("brief"); });
    rerender({ sid: null });
    act(() => { result.current.reset(); });
    expect(interruptCalls).toBe(0);
  });

  it("issue's tracking chain swallows a throwing refreshRunSummary without crashing (trailing rejection guard)", async () => {
    server.use(http.post(PROPOSE_URL, () => HttpResponse.json({ detail: "boom" }, { status: 502 })));
    const refresh: () => Promise<void> = vi.fn(() => {
      throw new Error("refresh boom");
    });
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
  });

  it("an issued-but-never-consumed prefetch still calls refreshRunSummary once it settles", async () => {
    server.use(http.post(PROPOSE_URL, () => HttpResponse.json(READY)));
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  });

  it("issuing and settling a prefetch causes no re-renders of the consuming component", async () => {
    server.use(http.post(PROPOSE_URL, () => HttpResponse.json(READY)));
    const refresh = vi.fn().mockResolvedValue(undefined);
    let renderCount = 0;
    const { result } = renderHook(() => {
      renderCount += 1;
      return useProposePrefetch(SID, refresh, DEPS);
    });
    const baseline = renderCount;
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(renderCount).toBe(baseline);
  });

  it("injection: issue calls the injected propose fn, not a hardcoded build-chat one", async () => {
    let injectedCalls = 0;
    const propose = vi.fn(() => {
      injectedCalls += 1;
      return Promise.resolve(READY);
    });
    const interrupt = vi.fn(() => Promise.resolve());
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useProposePrefetch(SID, refresh, { propose, interrupt }),
    );
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(injectedCalls).toBe(1));
    expect(propose).toHaveBeenCalledWith(SID);
  });

  it("injection: cancelBeforeLockingAction calls the injected interrupt fn", async () => {
    const propose = vi.fn(() => new Promise<DispatchProposalResponse>(() => undefined));
    let interruptCalls = 0;
    const interrupt = vi.fn(() => {
      interruptCalls += 1;
      return Promise.resolve();
    });
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useProposePrefetch(SID, refresh, { propose, interrupt }),
    );
    act(() => { result.current.issue("brief"); });
    const action = vi.fn().mockResolvedValue("sent");
    await expect(result.current.cancelBeforeLockingAction(action)).resolves.toBe("sent");
    expect(interruptCalls).toBe(4);
    expect(interrupt).toHaveBeenCalledWith(SID);
  });

  it("Explore-wiring: the same hook works against the explore-chat propose/interrupt deps", async () => {
    let proposeCalls = 0;
    server.use(
      http.post(EXPLORE_PROPOSE_URL, () => { proposeCalls += 1; return HttpResponse.json(READY); }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, EXPLORE_DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(proposeCalls).toBe(1));
    const outcome = result.current.consume(SID, "brief");
    expect(outcome).toEqual({ immediate: READY });
  });

  it("Explore-wiring: a mismatched consume interrupts via the explore-chat endpoint", async () => {
    let interruptCalls = 0;
    server.use(
      http.post(EXPLORE_PROPOSE_URL, () => new Promise<Response>(() => undefined)),
      http.post(EXPLORE_INTERRUPT_URL, () => {
        interruptCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, EXPLORE_DEPS));
    act(() => { result.current.issue("brief"); });
    const action = vi.fn().mockResolvedValue("sent");
    await expect(result.current.cancelBeforeLockingAction(action)).resolves.toBe("sent");
    expect(interruptCalls).toBeGreaterThan(0);
  });

  it("one-shot guard: issue stays a no-op right after a matching consume, since the consumed entry is retained rather than cleared", async () => {
    let proposeCalls = 0;
    server.use(
      http.post(PROPOSE_URL, () => { proposeCalls += 1; return HttpResponse.json(READY); }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(proposeCalls).toBe(1));
    const outcome = result.current.consume(SID, "brief");
    expect(outcome).toEqual({ immediate: READY });
    act(() => { result.current.issue("brief-again"); });
    expect(proposeCalls).toBe(1);
  });
});
