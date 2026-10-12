import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CounterCase, DispatchProposal, DispatchProposalResponse } from "../../../api";
import type { SummarizeFanout } from "./autoDispatch";
import type { CollisionInfo } from "./useCollisionCheck";
import {
  useAutoDispatchRun,
  type AdvisorAutoDispatchPlan,
  type ChatAutoDispatchPlan,
} from "./useAutoDispatchRun";

const COUNTER_CASE: CounterCase = { against: "a", cheapest_80: "b", cost_estimate: "c" };

const PROPOSAL: DispatchProposal = {
  team: "implementation_plan",
  rationale: "r",
  instructions: null,
  scope_audit: [],
  counter_case: COUNTER_CASE,
  title: "a-title",
  acceptance_clauses: [],
};

const GREEN: CollisionInfo = { status: "green", sentence: "clear", match: null };
const RED: CollisionInfo = { status: "red", sentence: "overlap!", match: null };

function readyResponse(): DispatchProposalResponse {
  return {
    status: "ready",
    proposal: PROPOSAL,
    collision: { state: "green", sentence: "clear", match: null },
  };
}

function needMoreInfo(): DispatchProposalResponse {
  return { status: "need_more_info", questions: ["which db?"] };
}

function makeChatPlan(overrides: Partial<ChatAutoDispatchPlan> = {}): ChatAutoDispatchPlan {
  const fanout: SummarizeFanout = {
    content: "the brief",
    collision: Promise.resolve(GREEN),
    proposal: Promise.resolve(readyResponse()),
  };
  return {
    kind: "chat",
    summarize: vi.fn().mockResolvedValue(fanout),
    commit: vi.fn().mockResolvedValue(true),
    ...overrides,
  };
}

function makeAdvisorPlan(overrides: Partial<AdvisorAutoDispatchPlan> = {}): AdvisorAutoDispatchPlan {
  return {
    kind: "advisor",
    propose: vi.fn().mockResolvedValue(readyResponse()),
    commit: vi.fn().mockResolvedValue(true),
    ...overrides,
  };
}

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("useAutoDispatchRun — chat plan", () => {
  it("starts idle", () => {
    const { result } = renderHook(() => useAutoDispatchRun());
    expect(result.current.state).toEqual({ running: false, stage: null, stop: null });
  });

  it("runs summarizing -> checking -> proposing -> dispatching -> idle on the happy path", async () => {
    const commit = vi.fn().mockResolvedValue(true);
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ commit })));
    await waitFor(() => expect(result.current.state).toEqual({ running: false, stage: null, stop: null }));
    expect(commit).toHaveBeenCalledWith({
      proposal: PROPOSAL,
      briefContent: "the brief",
      dispatchDescription: "the brief",
      collision: { state: "green", sentence: "clear", match: null },
    });
  });

  it("stops with summarize_failed when summarize resolves null", async () => {
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ summarize: vi.fn().mockResolvedValue(null) })));
    await waitFor(() => expect(result.current.state.stop).toBe("summarize_failed"));
    expect(result.current.state.running).toBe(false);
  });

  it("stops with overlap when the collision check does not come back green", async () => {
    const fanout: SummarizeFanout = {
      content: "brief",
      collision: Promise.resolve(RED),
      proposal: Promise.resolve(readyResponse()),
    };
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ summarize: vi.fn().mockResolvedValue(fanout) })));
    await waitFor(() => expect(result.current.state.stop).toBe("overlap"));
  });

  it("stops with propose_failed when the proposal promise resolves null", async () => {
    const fanout: SummarizeFanout = {
      content: "brief",
      collision: Promise.resolve(GREEN),
      proposal: Promise.resolve(null),
    };
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ summarize: vi.fn().mockResolvedValue(fanout) })));
    await waitFor(() => expect(result.current.state.stop).toBe("propose_failed"));
  });

  it("stops with needs_info when the proposal still has open questions", async () => {
    const fanout: SummarizeFanout = {
      content: "brief",
      collision: Promise.resolve(GREEN),
      proposal: Promise.resolve(needMoreInfo()),
    };
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ summarize: vi.fn().mockResolvedValue(fanout) })));
    await waitFor(() => expect(result.current.state.stop).toBe("needs_info"));
  });

  it("stops with dispatch_failed when commit reports failure", async () => {
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ commit: vi.fn().mockResolvedValue(false) })));
    await waitFor(() => expect(result.current.state.stop).toBe("dispatch_failed"));
  });

  it("reports each stage while running, in order", async () => {
    const seen: (string | null)[] = [];
    const summarizeD = deferred<SummarizeFanout | null>();
    const collisionD = deferred<CollisionInfo>();
    const proposalD = deferred<DispatchProposalResponse | null>();
    const commitD = deferred<boolean>();
    const fanout: SummarizeFanout = {
      content: "brief",
      collision: collisionD.promise,
      proposal: proposalD.promise,
    };
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() =>
      result.current.start(
        makeChatPlan({ summarize: () => summarizeD.promise, commit: () => commitD.promise }),
      ),
    );
    await waitFor(() => expect(result.current.state.stage).toBe("summarizing"));
    seen.push(result.current.state.stage);

    await act(async () => {
      summarizeD.resolve(fanout);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.state.stage).toBe("checking");
    seen.push(result.current.state.stage);

    await act(async () => {
      collisionD.resolve(GREEN);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.state.stage).toBe("proposing");
    seen.push(result.current.state.stage);

    await act(async () => {
      proposalD.resolve(readyResponse());
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.state.stage).toBe("dispatching");
    seen.push(result.current.state.stage);

    await act(async () => {
      commitD.resolve(true);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.state).toEqual({ running: false, stage: null, stop: null });
    expect(seen).toEqual(["summarizing", "checking", "proposing", "dispatching"]);
  });
});

describe("useAutoDispatchRun — advisor plan", () => {
  it("runs proposing -> checking -> dispatching -> idle on the happy path", async () => {
    const commit = vi.fn().mockResolvedValue(true);
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeAdvisorPlan({ commit })));
    await waitFor(() => expect(result.current.state).toEqual({ running: false, stage: null, stop: null }));
    expect(commit).toHaveBeenCalledWith(PROPOSAL);
  });

  it("stops with propose_failed when propose resolves null", async () => {
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeAdvisorPlan({ propose: vi.fn().mockResolvedValue(null) })));
    await waitFor(() => expect(result.current.state.stop).toBe("propose_failed"));
  });

  it("stops with needs_info when propose still has open questions", async () => {
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeAdvisorPlan({ propose: vi.fn().mockResolvedValue(needMoreInfo()) })));
    await waitFor(() => expect(result.current.state.stop).toBe("needs_info"));
  });

  it("stops with overlap when the proposal's own collision verdict is not green", async () => {
    const response: DispatchProposalResponse = {
      status: "ready",
      proposal: PROPOSAL,
      collision: { state: "red", sentence: "overlap", match: null },
    };
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeAdvisorPlan({ propose: vi.fn().mockResolvedValue(response) })));
    await waitFor(() => expect(result.current.state.stop).toBe("overlap"));
  });

  it("stops with dispatch_failed when commit reports failure", async () => {
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeAdvisorPlan({ commit: vi.fn().mockResolvedValue(false) })));
    await waitFor(() => expect(result.current.state.stop).toBe("dispatch_failed"));
  });
});

describe("useAutoDispatchRun — cancellation", () => {
  it("stop() sets the cancelled reason while running", async () => {
    const proposalD = deferred<DispatchProposalResponse | null>();
    const fanout: SummarizeFanout = {
      content: "brief",
      collision: Promise.resolve(GREEN),
      proposal: proposalD.promise,
    };
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ summarize: vi.fn().mockResolvedValue(fanout) })));
    await waitFor(() => expect(result.current.state.stage).toBe("proposing"));
    act(() => result.current.stop());
    expect(result.current.state).toEqual({ running: false, stage: null, stop: "cancelled" });
  });

  it("stop() is a no-op when nothing is running", () => {
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.stop());
    expect(result.current.state).toEqual({ running: false, stage: null, stop: null });
  });

  it("cancel() accepts a custom reason", async () => {
    const proposalD = deferred<DispatchProposalResponse | null>();
    const fanout: SummarizeFanout = {
      content: "brief",
      collision: Promise.resolve(GREEN),
      proposal: proposalD.promise,
    };
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ summarize: vi.fn().mockResolvedValue(fanout) })));
    await waitFor(() => expect(result.current.state.stage).toBe("proposing"));
    act(() => result.current.cancel("brief_edited"));
    expect(result.current.state).toEqual({ running: false, stage: null, stop: "brief_edited" });
  });

  it("a stale run resumes as a no-op after being cancelled — it never overwrites the newer state", async () => {
    const proposalD = deferred<DispatchProposalResponse | null>();
    const commit = vi.fn().mockResolvedValue(true);
    const fanout: SummarizeFanout = {
      content: "brief",
      collision: Promise.resolve(GREEN),
      proposal: proposalD.promise,
    };
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() => result.current.start(makeChatPlan({ summarize: vi.fn().mockResolvedValue(fanout), commit })));
    await waitFor(() => expect(result.current.state.stage).toBe("proposing"));
    act(() => result.current.cancel("brief_edited"));
    await act(async () => {
      proposalD.resolve(readyResponse());
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.state).toEqual({ running: false, stage: null, stop: "brief_edited" });
    expect(commit).not.toHaveBeenCalled();
  });

  it("starting a fresh run supersedes an in-flight one instead of racing it", async () => {
    const firstProposalD = deferred<DispatchProposalResponse | null>();
    const firstCommit = vi.fn().mockResolvedValue(true);
    const firstFanout: SummarizeFanout = {
      content: "first",
      collision: Promise.resolve(GREEN),
      proposal: firstProposalD.promise,
    };
    const secondCommit = vi.fn().mockResolvedValue(true);
    const { result } = renderHook(() => useAutoDispatchRun());
    act(() =>
      result.current.start(makeChatPlan({ summarize: vi.fn().mockResolvedValue(firstFanout), commit: firstCommit })),
    );
    await waitFor(() => expect(result.current.state.stage).toBe("proposing"));

    act(() => result.current.start(makeChatPlan({ commit: secondCommit })));
    await waitFor(() => expect(result.current.state).toEqual({ running: false, stage: null, stop: null }));
    expect(secondCommit).toHaveBeenCalledTimes(1);

    await act(async () => {
      firstProposalD.resolve(readyResponse());
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(firstCommit).not.toHaveBeenCalled();
  });
});
