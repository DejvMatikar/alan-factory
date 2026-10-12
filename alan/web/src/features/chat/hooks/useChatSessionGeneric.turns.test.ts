import { readFileSync } from "node:fs";
import path from "node:path";

import { act, renderHook, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import { chatErrorOf, isTurnRunning } from "./chatSessionReducer";
import { makeBinding, readyHook, SID, type TestBrief } from "../../../test/chatSessionHarness";
import { useChatSessionGeneric } from "./useChatSessionGeneric";

describe("useChatSessionGeneric turn transitions", () => {
  it("sends exactly once per user send under StrictMode, including a queued message flushed after the turn completes", async () => {
    const binding = makeBinding();
    const { result, emit } = await readyHook(binding, SID, StrictMode);

    await act(async () => {
      await result.current.sendMessage("a");
    });
    expect(binding.send).toHaveBeenCalledTimes(1);

    act(() => {
      void result.current.sendMessage("b");
    });
    await waitFor(() => expect(result.current.queuedMessage).toBe("b"));

    await emit("assistant_done", JSON.stringify({ content: "done" }));
    await waitFor(() => expect(binding.send).toHaveBeenCalledTimes(2));
    expect(binding.send).toHaveBeenNthCalledWith(2, SID, "b");
  });

  it("queues the second of two same-tick sends while a turn is running", async () => {
    const binding = makeBinding();
    const { result } = await readyHook(binding);

    act(() => {
      void result.current.sendMessage("a");
      void result.current.sendMessage("b");
    });

    await waitFor(() => expect(binding.send).toHaveBeenCalledTimes(1));
    expect(binding.send).toHaveBeenCalledWith(SID, "a");
    expect(result.current.queuedMessage).toBe("b");
  });

  it("retries a queued flush after a 409 without duplicating the transcript row", async () => {
    let calls = 0;
    const binding = makeBinding({
      send: vi.fn().mockImplementation((_sid: string, content: string) => {
        calls += 1;
        if (content === "a") return Promise.resolve({ status: "accepted" });
        if (calls === 2) return Promise.reject(new Error("409: locked"));
        return Promise.resolve({ status: "accepted" });
      }),
    });
    const { result, emit } = await readyHook(binding);

    await act(async () => {
      await result.current.sendMessage("a");
    });
    act(() => {
      void result.current.sendMessage("b");
    });
    await waitFor(() => expect(result.current.queuedMessage).toBe("b"));

    await emit("assistant_done", JSON.stringify({ content: "r" }));

    await waitFor(() => expect(calls).toBe(3), { timeout: 2000 });
    expect(result.current.queuedMessage).toBeNull();
    expect(result.current.messages.filter((m) => m.content === "b")).toHaveLength(1);
  });

  it("stops retrying a queued flush once the hook unmounts during backoff", async () => {
    let calls = 0;
    const binding = makeBinding({
      send: vi.fn().mockImplementation(() => {
        calls += 1;
        if (calls === 1) return Promise.resolve({ status: "accepted" });
        return Promise.reject(new Error("409: locked"));
      }),
    });
    const { result, emit, unmount } = await readyHook(binding);

    await act(async () => {
      await result.current.sendMessage("first");
    });
    act(() => {
      void result.current.sendMessage("queued");
    });
    await waitFor(() => expect(result.current.queuedMessage).toBe("queued"));

    await emit("assistant_done", JSON.stringify({ content: "reply" }));
    await waitFor(() => expect(calls).toBe(2));

    const callsAtUnmount = calls;
    unmount();
    await new Promise((r) => setTimeout(r, 350));
    expect(calls).toBe(callsAtUnmount);
  });

  it("ignores a flush success that resolves after the hook unmounts", async () => {
    let calls = 0;
    let resolveSecondSend!: (v: { status: string }) => void;
    const binding = makeBinding({
      send: vi.fn().mockImplementation(() => {
        calls += 1;
        if (calls === 1) return Promise.resolve({ status: "accepted" });
        return new Promise<{ status: string }>((resolve) => {
          resolveSecondSend = resolve;
        });
      }),
    });
    const { result, emit, unmount } = await readyHook(binding);

    await act(async () => {
      await result.current.sendMessage("first");
    });
    act(() => {
      void result.current.sendMessage("queued");
    });
    await waitFor(() => expect(result.current.queuedMessage).toBe("queued"));

    await emit("assistant_done", JSON.stringify({ content: "reply" }));
    await waitFor(() => expect(calls).toBe(2));

    unmount();
    resolveSecondSend({ status: "accepted" });
    await new Promise((r) => setTimeout(r, 10));
  });

  it("cancelling a queued flush during backoff prevents the scheduled retry", async () => {
    let calls = 0;
    const binding = makeBinding({
      send: vi.fn().mockImplementation(() => {
        calls += 1;
        if (calls === 1) return Promise.resolve({ status: "accepted" });
        return Promise.reject(new Error("409: locked"));
      }),
    });
    const { result, emit } = await readyHook(binding);

    await act(async () => {
      await result.current.sendMessage("first");
    });
    act(() => {
      void result.current.sendMessage("queued");
    });
    await waitFor(() => expect(result.current.queuedMessage).toBe("queued"));

    await emit("assistant_done", JSON.stringify({ content: "reply" }));
    await waitFor(() => expect(calls).toBe(2));

    act(() => {
      result.current.cancelQueued();
    });
    expect(result.current.queuedMessage).toBeNull();

    await new Promise((r) => setTimeout(r, 350));
    expect(calls).toBe(2);
  });

  it("lets a send on a failed-load session clear the banner and start a running turn", async () => {
    const binding = makeBinding({
      get: vi.fn().mockRejectedValue(new Error("500: boom")),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("load_failed"));
    expect(chatErrorOf(result.current.turn)).toBe("500: boom");

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    expect(chatErrorOf(result.current.turn)).toBeNull();
    expect(isTurnRunning(result.current.turn)).toBe(true);
    expect(binding.send).toHaveBeenCalledTimes(1);
  });

  describe("source scan", () => {
    it("keeps useChatSessionGeneric.ts to at most one useRef( call site and zero eslint-disable comments", () => {
      const source = readFileSync(
        path.resolve(process.cwd(), "src/features/chat/hooks/useChatSessionGeneric.ts"),
        "utf-8",
      );
      expect(source.match(/useRef\(/g) ?? []).toHaveLength(1);
      expect(source).not.toContain("eslint-disable");
    });
  });
});
