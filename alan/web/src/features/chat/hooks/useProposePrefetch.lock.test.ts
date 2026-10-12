import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { api, type DispatchProposalResponse } from "../../../api";
import { server } from "../../../test/server";
import { useProposePrefetch } from "./useProposePrefetch";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const PROPOSE_URL = `/api/build-chat/sessions/${SID}/propose`;
const INTERRUPT_URL = `/api/build-chat/sessions/${SID}/interrupt`;

const DEPS = { propose: api.proposeBuildDispatch, interrupt: api.interruptBuildChatSession };

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

describe("useProposePrefetch — consumed-entry lock semantics", () => {
  it("consume retains the entry (marked consumed) rather than clearing it, so a same-brief re-consume can't silently replay the already-handed-off result", async () => {
    let proposeCalls = 0;
    server.use(
      http.post(PROPOSE_URL, () => {
        proposeCalls += 1;
        return HttpResponse.json({ ...READY, proposal: { ...READY.proposal, rationale: `r${String(proposeCalls)}` } });
      }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(proposeCalls).toBe(1));

    const first = result.current.consume(SID, "brief");
    expect(first).toEqual({ immediate: { ...READY, proposal: { ...READY.proposal, rationale: "r1" } } });

    const second = result.current.consume(SID, "brief");
    expect("pending" in second).toBe(true);
    if (!("pending" in second)) throw new Error("expected pending");
    await expect(second.pending).resolves.toEqual({
      ...READY,
      proposal: { ...READY.proposal, rationale: "r2" },
    });
    expect(proposeCalls).toBe(2);
  });

  it("consume-then-locking-action race: cancelBeforeLockingAction still finds and cancels a just-consumed, not-yet-settled entry before running the action", async () => {
    let interruptCalls = 0;
    let resolvePropose!: (v: DispatchProposalResponse) => void;
    server.use(
      http.post(PROPOSE_URL, () =>
        new Promise((resolve) => {
          resolvePropose = (v) => resolve(HttpResponse.json(v));
        }),
      ),
      http.post(INTERRUPT_URL, () => {
        interruptCalls += 1;
        if (interruptCalls === 1) resolvePropose(READY);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });

    const outcome = result.current.consume(SID, "brief");
    expect("pending" in outcome).toBe(true);

    const action = vi.fn().mockResolvedValue("sent");
    await expect(result.current.cancelBeforeLockingAction(action)).resolves.toBe("sent");
    expect(interruptCalls).toBeGreaterThanOrEqual(1);
    expect(action).toHaveBeenCalledTimes(1);
  });

  it("a mismatched consume after a completed consume skips the interrupt call entirely — cancelPrefetchLock's isSettled check short-circuits before ever calling it", async () => {
    let proposeCalls = 0;
    let interruptCalls = 0;
    server.use(
      http.post(PROPOSE_URL, () => {
        proposeCalls += 1;
        return HttpResponse.json(READY);
      }),
      http.post(INTERRUPT_URL, () => { interruptCalls += 1; return new HttpResponse(null, { status: 204 }); }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useProposePrefetch(SID, refresh, DEPS));
    act(() => { result.current.issue("brief"); });
    await waitFor(() => expect(proposeCalls).toBe(1));

    const first = result.current.consume(SID, "brief");
    expect(first).toEqual({ immediate: READY });

    const second = result.current.consume(SID, "edited brief");
    expect("pending" in second).toBe(true);
    if (!("pending" in second)) throw new Error("expected pending");
    await expect(second.pending).resolves.toEqual(READY);
    expect(interruptCalls).toBe(0);
    expect(proposeCalls).toBe(2);
  });
});
