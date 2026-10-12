import { act, renderHook, waitFor } from "@testing-library/react";
import type { FormEvent } from "react";
import { describe, expect, it, vi } from "vitest";

import { useChatComposerActions } from "./useChatComposerActions";

interface TestChat {
  sendMessage: (content: string) => Promise<void>;
  clear: () => Promise<string>;
  summarize: () => Promise<{ content: string }>;
  updateBrief: (content: string) => Promise<unknown>;
  interrupt: () => Promise<void>;
  restoredDraft: string | null;
  clearRestoredDraft: () => void;
}

function makeChat(overrides: Partial<TestChat> = {}): TestChat {
  return {
    sendMessage: vi.fn().mockResolvedValue(undefined),
    clear: vi.fn().mockResolvedValue("new-session-id"),
    summarize: vi.fn().mockResolvedValue({ content: "summary" }),
    updateBrief: vi.fn().mockResolvedValue(undefined),
    interrupt: vi.fn().mockResolvedValue(undefined),
    restoredDraft: null,
    clearRestoredDraft: vi.fn(),
    ...overrides,
  };
}

describe("useChatComposerActions", () => {
  it("starts with the given initial draft and no transient state", () => {
    const chat = makeChat();
    const { result } = renderHook(() =>
      useChatComposerActions(chat, vi.fn(), vi.fn(), "hello"),
    );
    expect(result.current.draft).toBe("hello");
    expect(result.current.summarizing).toBe(false);
    expect(result.current.summarizeError).toBeNull();
    expect(result.current.stopping).toBe(false);
  });

  it("handleSend trims, clears the draft, and sends", () => {
    const chat = makeChat();
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
    act(() => result.current.setDraft("  hi  "));
    act(() => {
      result.current.handleSend({ preventDefault: vi.fn() } as unknown as FormEvent);
    });
    expect(chat.sendMessage).toHaveBeenCalledWith("hi");
    expect(result.current.draft).toBe("");
  });

  it("handleSend ignores whitespace-only drafts", () => {
    const chat = makeChat();
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
    act(() => result.current.setDraft("   "));
    act(() => {
      result.current.handleSend({ preventDefault: vi.fn() } as unknown as FormEvent);
    });
    expect(chat.sendMessage).not.toHaveBeenCalled();
  });

  it("handleSend silently swallows a rejected sendMessage", async () => {
    const chat = makeChat({ sendMessage: vi.fn().mockRejectedValue(new Error("boom")) });
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
    act(() => result.current.setDraft("hi"));
    await act(async () => {
      result.current.handleSend({ preventDefault: vi.fn() } as unknown as FormEvent);
      await Promise.resolve();
    });
  });

  it("handleClear does nothing when the user cancels the confirm dialog", () => {
    const chat = makeChat();
    vi.spyOn(globalThis, "confirm").mockReturnValue(false);
    const onCleared = vi.fn();
    const { result } = renderHook(() => useChatComposerActions(chat, onCleared, vi.fn()));
    act(() => result.current.handleClear());
    expect(chat.clear).not.toHaveBeenCalled();
    expect(onCleared).not.toHaveBeenCalled();
  });

  it("handleClear clears the session and notifies onCleared when confirmed", async () => {
    const chat = makeChat();
    vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    const onCleared = vi.fn();
    const { result } = renderHook(() => useChatComposerActions(chat, onCleared, vi.fn()));
    act(() => result.current.handleClear());
    await waitFor(() => expect(onCleared).toHaveBeenCalledWith("new-session-id"));
  });

  it("handleSummarize resolves and calls onSummarized with the brief content", async () => {
    const chat = makeChat();
    const onSummarized = vi.fn();
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), onSummarized));
    act(() => result.current.handleSummarize());
    expect(result.current.summarizing).toBe(true);
    await waitFor(() => expect(result.current.summarizing).toBe(false));
    expect(onSummarized).toHaveBeenCalledWith("summary");
    expect(result.current.summarizeError).toBeNull();
  });

  it("handleSummarize surfaces a summarize error", async () => {
    const chat = makeChat({ summarize: vi.fn().mockRejectedValue(new Error("summarize failed")) });
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
    act(() => result.current.handleSummarize());
    await waitFor(() => expect(result.current.summarizeError).toBe("summarize failed"));
    expect(result.current.summarizing).toBe(false);
  });

  it("handleBriefEdit calls updateBrief and swallows rejection", async () => {
    const chat = makeChat({ updateBrief: vi.fn().mockRejectedValue(new Error("boom")) });
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
    await act(async () => {
      result.current.handleBriefEdit("edited content");
      await Promise.resolve();
    });
    expect(chat.updateBrief).toHaveBeenCalledWith("edited content");
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
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
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
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
    await act(async () => {
      result.current.handleStop();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.stopping).toBe(false);
  });

  it("restores a queued draft from the chat and clears it once", async () => {
    const chat = makeChat({ restoredDraft: "queued follow-up" });
    const { result } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
    await waitFor(() => expect(result.current.draft).toBe("queued follow-up"));
    expect(chat.clearRestoredDraft).toHaveBeenCalledTimes(1);
  });

  it("does not touch the draft when restoredDraft is null", () => {
    const chat = makeChat({ restoredDraft: null });
    const { result } = renderHook(() =>
      useChatComposerActions(chat, vi.fn(), vi.fn(), "untouched"),
    );
    expect(result.current.draft).toBe("untouched");
    expect(chat.clearRestoredDraft).not.toHaveBeenCalled();
  });

  it("does not clobber a freshly typed draft when a restoredDraft arrives after typing", () => {
    const chat = makeChat();
    const { result, rerender } = renderHook(
      (props: TestChat) => useChatComposerActions(props, vi.fn(), vi.fn()),
      { initialProps: chat },
    );
    act(() => result.current.setDraft("fresh replacement"));
    rerender({ ...chat, restoredDraft: "queued follow-up" });
    expect(chat.clearRestoredDraft).toHaveBeenCalledTimes(1);
    expect(result.current.draft).toBe("fresh replacement");
  });

  it("does not update stopping state after unmount while interrupt is still in flight", async () => {
    let resolveInterrupt!: () => void;
    const chat = makeChat({
      interrupt: vi.fn().mockReturnValue(
        new Promise<void>((resolve) => {
          resolveInterrupt = resolve;
        }),
      ),
    });
    const { result, unmount } = renderHook(() => useChatComposerActions(chat, vi.fn(), vi.fn()));
    act(() => result.current.handleStop());
    expect(result.current.stopping).toBe(true);
    unmount();
    await act(async () => {
      resolveInterrupt();
      await Promise.resolve();
    });
  });
});
