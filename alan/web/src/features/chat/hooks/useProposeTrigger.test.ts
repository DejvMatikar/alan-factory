import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DispatchProposalResponse } from "../../../api";
import { useProposeTrigger, type ChatForComposerBase } from "./useProposeTrigger";
import type { UseProposePrefetchResult } from "./useProposePrefetch";

const SID = "sess-1";

interface FakeBrief {
  content: string;
}

function makeChat(overrides: Partial<ChatForComposerBase<FakeBrief>> = {}): ChatForComposerBase<FakeBrief> & { extra: string } {
  return {
    sendMessage: vi.fn().mockResolvedValue(undefined),
    summarize: vi.fn().mockResolvedValue({ content: "summarized" }),
    updateBrief: vi.fn().mockResolvedValue({ content: "updated" }),
    refreshRunSummary: vi.fn().mockResolvedValue(undefined),
    extra: "preserved-field",
    ...overrides,
  };
}

function defaultCancelBeforeLockingAction<T>(action: () => Promise<T>): Promise<T> {
  return action();
}

function makePrefetch(overrides: Partial<UseProposePrefetchResult> = {}): UseProposePrefetchResult {
  return {
    issue: vi.fn(),
    consume: vi.fn(),
    cancelBeforeLockingAction: vi.fn(defaultCancelBeforeLockingAction) as UseProposePrefetchResult["cancelBeforeLockingAction"],
    discardStale: vi.fn(),
    reset: vi.fn(),
    ...overrides,
  };
}

const READY: DispatchProposalResponse = {
  status: "ready",
  proposal: {
    team: "brainstorming",
    rationale: "r",
    instructions: null,
    scope_audit: [],
    counter_case: { against: "a", cheapest_80: "b", cost_estimate: "c" },
  },
  collision: { state: "green", sentence: "ok", match: null },
};

describe("useProposeTrigger", () => {
  it("triggerPropose is a no-op when sessionId is null, and resolves to null", async () => {
    const chat = makeChat();
    const prefetch = makePrefetch();
    const resolveImmediate = vi.fn();
    const resolvePending = vi.fn();
    const { result } = renderHook(() =>
      useProposeTrigger(null, chat, prefetch, { resolveImmediate, resolvePending }),
    );
    await expect(result.current.triggerPropose("some content")).resolves.toBeNull();
    expect(prefetch.consume).not.toHaveBeenCalled();
  });

  it("triggerPropose is a no-op when content is blank, and resolves to null", async () => {
    const chat = makeChat();
    const prefetch = makePrefetch();
    const resolveImmediate = vi.fn();
    const resolvePending = vi.fn();
    const { result } = renderHook(() =>
      useProposeTrigger(SID, chat, prefetch, { resolveImmediate, resolvePending }),
    );
    await expect(result.current.triggerPropose("   ")).resolves.toBeNull();
    expect(prefetch.consume).not.toHaveBeenCalled();
  });

  it("routes an immediate outcome to resolveImmediate and returns its result", async () => {
    const chat = makeChat();
    const prefetch = makePrefetch({
      consume: vi.fn().mockReturnValue({ immediate: READY }),
    });
    const resolveImmediate = vi.fn().mockResolvedValue(READY);
    const resolvePending = vi.fn();
    const { result } = renderHook(() =>
      useProposeTrigger(SID, chat, prefetch, { resolveImmediate, resolvePending }),
    );
    await expect(result.current.triggerPropose("brief")).resolves.toBe(READY);
    expect(prefetch.consume).toHaveBeenCalledWith(SID, "brief");
    expect(resolveImmediate).toHaveBeenCalledWith(READY, "brief");
    expect(resolvePending).not.toHaveBeenCalled();
  });

  it("routes a pending outcome to resolvePending, refreshing on settle when the outcome asks for it", () => {
    const chat = makeChat();
    const pending = Promise.resolve(READY);
    const prefetch = makePrefetch({
      consume: vi.fn().mockReturnValue({ pending, refreshOnSettle: true }),
    });
    const resolveImmediate = vi.fn();
    const resolvePending = vi.fn();
    const { result } = renderHook(() =>
      useProposeTrigger(SID, chat, prefetch, { resolveImmediate, resolvePending }),
    );
    void result.current.triggerPropose("brief");
    expect(resolvePending).toHaveBeenCalledTimes(1);
    const [passedPending, passedBrief, onSettleExtra] = resolvePending.mock.calls[0] as [
      Promise<DispatchProposalResponse>,
      string,
      (() => void) | undefined,
    ];
    expect(passedPending).toBe(pending);
    expect(passedBrief).toBe("brief");
    onSettleExtra?.();
    expect(chat.refreshRunSummary).toHaveBeenCalledTimes(1);
  });

  it("does not refresh the run summary on settle when the outcome says not to", () => {
    const chat = makeChat();
    const prefetch = makePrefetch({
      consume: vi.fn().mockReturnValue({ pending: Promise.resolve(READY), refreshOnSettle: false }),
    });
    const resolvePending = vi.fn();
    const { result } = renderHook(() =>
      useProposeTrigger(SID, chat, prefetch, { resolveImmediate: vi.fn(), resolvePending }),
    );
    void result.current.triggerPropose("brief");
    const onSettleExtra = resolvePending.mock.calls[0]?.[2] as (() => void) | undefined;
    onSettleExtra?.();
    expect(chat.refreshRunSummary).not.toHaveBeenCalled();
  });

  it("chatForComposer preserves the rest of the chat object's fields untouched", () => {
    const chat = makeChat();
    const prefetch = makePrefetch();
    const { result } = renderHook(() =>
      useProposeTrigger(SID, chat, prefetch, { resolveImmediate: vi.fn(), resolvePending: vi.fn() }),
    );
    expect(result.current.chatForComposer.extra).toBe("preserved-field");
  });

  it("chatForComposer.sendMessage routes through cancelBeforeLockingAction", async () => {
    const chat = makeChat();
    const prefetch = makePrefetch();
    const { result } = renderHook(() =>
      useProposeTrigger(SID, chat, prefetch, { resolveImmediate: vi.fn(), resolvePending: vi.fn() }),
    );
    await result.current.chatForComposer.sendMessage("hi");
    expect(prefetch.cancelBeforeLockingAction).toHaveBeenCalledTimes(1);
    expect(chat.sendMessage).toHaveBeenCalledWith("hi");
  });

  it("chatForComposer.summarize routes through cancelBeforeLockingAction", async () => {
    const chat = makeChat();
    const prefetch = makePrefetch();
    const { result } = renderHook(() =>
      useProposeTrigger(SID, chat, prefetch, { resolveImmediate: vi.fn(), resolvePending: vi.fn() }),
    );
    await result.current.chatForComposer.summarize();
    expect(prefetch.cancelBeforeLockingAction).toHaveBeenCalledTimes(1);
    expect(chat.summarize).toHaveBeenCalledTimes(1);
  });

  it("chatForComposer.updateBrief discards any stale prefetch before saving", async () => {
    const chat = makeChat();
    const prefetch = makePrefetch();
    const { result } = renderHook(() =>
      useProposeTrigger(SID, chat, prefetch, { resolveImmediate: vi.fn(), resolvePending: vi.fn() }),
    );
    await result.current.chatForComposer.updateBrief("edited");
    expect(prefetch.discardStale).toHaveBeenCalledTimes(1);
    expect(chat.updateBrief).toHaveBeenCalledWith("edited");
  });
});
