import { act, renderHook, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { useStopWithDraftRestore } from "./useStopWithDraftRestore";

interface TestChat {
  interrupt: () => Promise<void>;
  restoredDraft: string | null;
  clearRestoredDraft: () => void;
}

function makeChat(overrides: Partial<TestChat> = {}): TestChat {
  return {
    interrupt: vi.fn().mockResolvedValue(undefined),
    restoredDraft: null,
    clearRestoredDraft: vi.fn(),
    ...overrides,
  };
}

function useHarness(chat: TestChat, initialDraft = "") {
  const [draft, setDraft] = useState(initialDraft);
  return { ...useStopWithDraftRestore(chat, draft, setDraft), draft, setDraft };
}

describe("useStopWithDraftRestore", () => {
  it("starts not stopping", () => {
    const chat = makeChat();
    const { result } = renderHook(() => useHarness(chat));
    expect(result.current.stopping).toBe(false);
  });

  it("handleStop sets stopping true then false once interrupt resolves", async () => {
    let resolveInterrupt!: () => void;
    const chat = makeChat({
      interrupt: vi.fn().mockReturnValue(
        new Promise<void>((resolve) => {
          resolveInterrupt = resolve;
        }),
      ),
    });
    const { result } = renderHook(() => useHarness(chat));
    act(() => result.current.handleStop());
    expect(result.current.stopping).toBe(true);
    await act(async () => {
      resolveInterrupt();
      await Promise.resolve();
    });
    expect(result.current.stopping).toBe(false);
  });

  it("handleStop clears stopping even when interrupt rejects", async () => {
    const chat = makeChat({ interrupt: vi.fn().mockRejectedValue(new Error("boom")) });
    const { result } = renderHook(() => useHarness(chat));
    await act(async () => {
      result.current.handleStop();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.stopping).toBe(false);
  });

  it("lets an interrupt that settles after unmount finish without error", async () => {
    let resolveInterrupt!: () => void;
    const chat = makeChat({
      interrupt: vi.fn().mockReturnValue(
        new Promise<void>((resolve) => {
          resolveInterrupt = resolve;
        }),
      ),
    });
    const { result, unmount } = renderHook(() => useHarness(chat));
    act(() => result.current.handleStop());
    expect(result.current.stopping).toBe(true);
    unmount();
    await act(async () => {
      resolveInterrupt();
      await Promise.resolve();
    });
    expect(chat.interrupt).toHaveBeenCalledOnce();
  });

  it("restores a queued draft from the chat and clears it once", async () => {
    const chat = makeChat({ restoredDraft: "queued follow-up" });
    const { result } = renderHook(() => useHarness(chat));
    await waitFor(() => expect(result.current.draft).toBe("queued follow-up"));
    expect(chat.clearRestoredDraft).toHaveBeenCalledTimes(1);
  });

  it("does not touch the draft when restoredDraft is null", () => {
    const chat = makeChat({ restoredDraft: null });
    const { result } = renderHook(() => useHarness(chat, "untouched"));
    expect(result.current.draft).toBe("untouched");
    expect(chat.clearRestoredDraft).not.toHaveBeenCalled();
  });

  it("does not clobber a freshly typed draft when a restoredDraft arrives after typing", () => {
    const chat = makeChat();
    const { result, rerender } = renderHook(
      (props: TestChat) => useHarness(props),
      { initialProps: chat },
    );
    act(() => result.current.setDraft("fresh replacement"));
    rerender({ ...chat, restoredDraft: "queued follow-up" });
    expect(chat.clearRestoredDraft).toHaveBeenCalledTimes(1);
    expect(result.current.draft).toBe("fresh replacement");
  });
});
