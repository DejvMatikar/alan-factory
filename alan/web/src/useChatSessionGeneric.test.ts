import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  useChatSessionGeneric,
  type ChatSessionApi,
  type UseGenericChatSessionResult,
} from "./features/chat/hooks/useChatSessionGeneric";
import {
  ArchivedSessionRedirect,
  CHAT_SSE_EVENT_KINDS,
  type BuildChatSessionState,
  type ChatEventHandlers,
} from "./api";
import { archivedTaskIdOf, chatErrorOf, isTurnRunning } from "./features/chat/hooks/chatSessionReducer";
import { makeBinding, SID, SID2, type TestBrief } from "./test/chatSessionHarness";
import { makeChatMessage, makeChatSession, makeRunSummary } from "./test/chatFixtures";

describe("useChatSessionGeneric", () => {
  it("returns loading state when sessionId is null", () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(null, binding));
    expect(result.current.session.kind).toBe("loading");
    expect(result.current.messages).toEqual([]);
    expect(result.current.brief).toBeNull();
  });

  it("loads session and transitions to ready", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(binding.get).toHaveBeenCalledWith(SID);
  });

  it("sets a load_failed session when session load fails", async () => {
    const binding = makeBinding({
      get: vi.fn().mockRejectedValue(new Error("404: not found")),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("load_failed"));
    expect(chatErrorOf(result.current.turn)).toContain("404");
  });

  it("surfaces non-Error load failure via String(err)", async () => {
    const binding = makeBinding({
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      get: vi.fn().mockReturnValue(Promise.reject("string-err")),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("load_failed"));
    expect(chatErrorOf(result.current.turn)).toBe("string-err");
  });

  it("sets an archived session when the session load rejects with ArchivedSessionRedirect", async () => {
    const binding = makeBinding({
      get: vi.fn().mockRejectedValue(new ArchivedSessionRedirect("archived-ws")),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(archivedTaskIdOf(result.current.session)).toBe("archived-ws"));
    expect(chatErrorOf(result.current.turn)).toBeNull();
    expect(result.current.session.kind).not.toBe("load_failed");
  });

  it("resets the session away from archived when sessionId changes away from an archived redirect", async () => {
    const binding = makeBinding({
      get: vi.fn().mockImplementation((sid: string) =>
        sid === SID
          ? Promise.reject(new ArchivedSessionRedirect("archived-ws"))
          : Promise.resolve(makeChatSession()),
      ),
    });
    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => useChatSessionGeneric<TestBrief>(id, binding),
      { initialProps: { id: SID } },
    );
    await waitFor(() => expect(archivedTaskIdOf(result.current.session)).toBe("archived-ws"));
    rerender({ id: SID2 });
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(archivedTaskIdOf(result.current.session)).toBeNull();
  });

  it("ignores an ArchivedSessionRedirect that resolves after unmount", async () => {
    let rejectGet!: (err: ArchivedSessionRedirect) => void;
    const binding = makeBinding({
      get: vi.fn().mockReturnValue(
        new Promise<never>((_res, rej) => {
          rejectGet = rej;
        }),
      ),
    });
    const { unmount } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    unmount();
    rejectGet(new ArchivedSessionRedirect("archived-ws"));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("ignores load failure after unmount", async () => {
    let rejectGet!: (err: Error) => void;
    const binding = makeBinding({
      get: vi.fn().mockReturnValue(
        new Promise<never>((_res, rej) => {
          rejectGet = rej;
        }),
      ),
    });
    const { unmount } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    unmount();
    rejectGet(new Error("late-fail"));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("starts a running turn when in_flight_turn is present on load", async () => {
    const binding = makeBinding({
      get: vi.fn().mockResolvedValue({
        messages: [],
        in_flight_turn: { started_at: "now", user_text: "hi" },
        brief: null,
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(isTurnRunning(result.current.turn)).toBe(true);
  });

  it("loads projectId and repoProvision from the session GET response", async () => {
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValue(makeChatSession({ project_id: "proj-1", repo_provision: "ready" })),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.projectId).toBe("proj-1");
    expect(result.current.repoProvision).toBe("ready");
  });

  it("defaults projectId to null when the session GET omits it and takes repoProvision from the wire", async () => {
    const binding = makeBinding({
      get: vi.fn().mockResolvedValue(makeChatSession({ repo_provision: "unchecked" })),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.projectId).toBeNull();
    expect(result.current.repoProvision).toBe("unchecked");
  });

  it("loads brief from session", async () => {
    const binding = makeBinding({
      get: vi.fn().mockResolvedValue({ ...makeChatSession(), brief: { content: "loaded brief" } }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.brief).toEqual({ content: "loaded brief" });
  });

  it("sends a message and starts a running turn", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.sendMessage("hello");
    });
    expect(binding.send).toHaveBeenCalledWith(SID, "hello");
    expect(isTurnRunning(result.current.turn)).toBe(true);
  });

  it("sendMessage throws when sessionId is null", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(null, binding));
    await expect(
      act(async () => {
        await result.current.sendMessage("hi");
      }),
    ).rejects.toThrow("no session");
  });

  it("queues a second message while thinking", async () => {
    const binding = makeBinding({
      send: vi.fn().mockResolvedValue({ status: "accepted" }),
      subscribe: vi.fn().mockReturnValue(() => undefined),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.sendMessage("first");
    });
    expect(isTurnRunning(result.current.turn)).toBe(true);
    await act(async () => {
      await result.current.sendMessage("queued");
    });
    expect(result.current.queuedMessage).toBe("queued");
  });

  it("cancelQueued clears the queued message", async () => {
    const binding = makeBinding({
      send: vi.fn().mockResolvedValue({ status: "accepted" }),
      subscribe: vi.fn().mockReturnValue(() => undefined),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.sendMessage("first");
    });
    expect(isTurnRunning(result.current.turn)).toBe(true);
    await act(async () => {
      await result.current.sendMessage("second");
    });
    expect(result.current.queuedMessage).toBe("second");
    act(() => {
      result.current.cancelQueued();
    });
    expect(result.current.queuedMessage).toBeNull();
  });

  it("interrupt is a no-op when sessionId is null", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(null, binding));
    await expect(result.current.interrupt()).resolves.toBeUndefined();
    expect(binding.interrupt).not.toHaveBeenCalled();
  });

  it("interrupt calls the binding", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.interrupt();
    });
    expect(binding.interrupt).toHaveBeenCalledWith(SID);
  });

  it("interrupt surfaces and re-throws binding errors", async () => {
    const binding = makeBinding({
      interrupt: vi.fn().mockRejectedValue(new Error("500: boom")),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    let thrown: unknown = null;
    await act(async () => {
      try {
        await result.current.interrupt();
      } catch (e) {
        thrown = e;
      }
    });
    expect((thrown as Error).message).toContain("500");
    expect(chatErrorOf(result.current.turn)).toContain("500");
  });

  it("summarize throws when sessionId is null", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(null, binding));
    await expect(
      act(async () => {
        await result.current.summarize!();
      }),
    ).rejects.toThrow("no session");
  });

  it("summarize updates the brief", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    let brief: TestBrief | undefined;
    await act(async () => {
      brief = await result.current.summarize!();
    });
    expect(brief).toEqual({ content: "summary" });
    expect(result.current.brief).toEqual({ content: "summary" });
  });

  it("does not replay the session load when only the binding identity changes", async () => {
    const binding = makeBinding();
    const { result, rerender } = renderHook(
      ({ b }: { b: ChatSessionApi<TestBrief> }) => useChatSessionGeneric<TestBrief>(SID, b),
      { initialProps: { b: binding } },
    );
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.summarize!();
    });
    expect(result.current.brief).toEqual({ content: "summary" });
    const callsAfterSummarize = (binding.get as ReturnType<typeof vi.fn>).mock.calls.length;
    expect(callsAfterSummarize).toBe(2);

    const rebuiltBinding = makeBinding();
    rerender({ b: rebuiltBinding });

    expect(binding.get).toHaveBeenCalledTimes(callsAfterSummarize);
    expect(rebuiltBinding.get).not.toHaveBeenCalled();
    expect(result.current.brief).toEqual({ content: "summary" });
  });

  it("updateBrief throws when sessionId is null", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(null, binding));
    await expect(
      act(async () => {
        await result.current.updateBrief!("new content");
      }),
    ).rejects.toThrow("no session");
  });

  it("updateBrief calls putBrief and updates state", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.updateBrief!("edited");
    });
    expect(binding.putBrief).toHaveBeenCalledWith(SID, "edited");
    expect(result.current.brief).toEqual({ content: "updated" });
  });

  it("clear skips delete when sessionId is null", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(null, binding));
    let newId = "";
    await act(async () => {
      newId = await result.current.clear();
    });
    expect(newId).toBe(SID2);
    expect(binding.delete).not.toHaveBeenCalled();
    expect(binding.createNew).toHaveBeenCalled();
  });

  it("clear deletes old session and creates a new one", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    let newId = "";
    await act(async () => {
      newId = await result.current.clear();
    });
    expect(newId).toBe(SID2);
    expect(binding.delete).toHaveBeenCalledWith(SID);
    expect(binding.createNew).toHaveBeenCalled();
  });

  it("applies assistant_done event to messages", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onEvent({
        event: "assistant_done",
        data: JSON.stringify({ content: "hello reply" }),
      });
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    expect(result.current.messages[0]?.content).toBe("hello reply");
    expect(isTurnRunning(result.current.turn)).toBe(false);
  });

  it("uses empty content when assistant_done payload is malformed JSON", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onEvent({ event: "assistant_done", data: "not-json" });
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    expect(result.current.messages[0]?.content).toBe("");
    expect(isTurnRunning(result.current.turn)).toBe(false);
  });

  it("surfaces error event detail", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onEvent({ event: "error", data: '{"detail":"boom"}' });
      await Promise.resolve();
    });
    await waitFor(() => expect(chatErrorOf(result.current.turn)).toBe("boom"));
  });

  it("surfaces raw error data when it isn't valid JSON", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onEvent({ event: "error", data: "raw text" });
      await Promise.resolve();
    });
    await waitFor(() => expect(chatErrorOf(result.current.turn)).toBe("raw text"));
  });

  it("surfaces the raw payload when JSON parses but detail is not a string", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onEvent({ event: "error", data: '{"detail":123}' });
      await Promise.resolve();
    });
    await waitFor(() => expect(chatErrorOf(result.current.turn)).toBe('{"detail":123}'));
  });

  it("refreshes projectId/repoProvision after an error event", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockResolvedValueOnce(makeChatSession({ project_id: "proj-3", repo_provision: "error" })),
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onEvent({ event: "error", data: '{"detail":"boom"}' });
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.repoProvision).toBe("error"));
    expect(result.current.projectId).toBe("proj-3");
  });

  it("resets state when sessionId switches to null", async () => {
    const binding = makeBinding({
      get: vi.fn().mockResolvedValue(
        makeChatSession({ messages: [makeChatMessage({ role: "user", content: "hi", ts: "t" })] }),
      ),
    });
    const initial: { id: string | null } = { id: SID };
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useChatSessionGeneric<TestBrief>(id, binding),
      { initialProps: initial },
    );
    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    rerender({ id: null });
    expect(result.current.messages).toEqual([]);
    expect(result.current.brief).toBeNull();
  });

  it("applies tool_use chat_event to toolActivity", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    const inner = JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "tool_use", name: "Read", input: { file_path: "foo.ts" } }] },
    });
    const outer = JSON.stringify({ event: inner });
    await act(async () => {
      handlerHolder.handlers?.onEvent({ event: "chat_event", data: outer });
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.toolActivity).toHaveLength(1));
    expect(result.current.toolActivity[0]?.label).toBe("Read: foo.ts");
  });

  it("ignores a chat_event whose label cannot be parsed", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onEvent({ event: "chat_event", data: "not-json" });
      await Promise.resolve();
    });
    expect(result.current.toolActivity).toEqual([]);
  });

  it("onError from subscribe surfaces as a turn error", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const binding = makeBinding({
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onError("stream error");
      await Promise.resolve();
    });
    expect(chatErrorOf(result.current.turn)).toBe("stream error");
  });

  it("loads runSummary from the session GET response", async () => {
    const runSummary = {
      model: "opus",
      effort: "xhigh",
      system_prompt: "prompt",
      total_cost_usd: 0.5,
      total_output_tokens: 100,
      total_input_tokens: 200,
      tool_counts: [{ name: "Read", count: 1 }],
    };
    const binding = makeBinding({
      get: vi.fn().mockResolvedValue(makeChatSession({ run_summary: runSummary })),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.runSummary).toEqual(runSummary);
  });

  it("loads collisionRunSummary from the session GET response", async () => {
    const collisionRunSummary = {
      model: "haiku",
      effort: null,
      system_prompt: "collision prompt",
      total_cost_usd: 0.001,
      total_output_tokens: 5,
      total_input_tokens: 8,
      tool_counts: [],
    };
    const binding = makeBinding({
      get: vi.fn().mockResolvedValue(makeChatSession({ collision_run_summary: collisionRunSummary })),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.collisionRunSummary).toEqual(collisionRunSummary);
  });

  it("defaults collisionRunSummary to null when the session GET omits it", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.collisionRunSummary).toBeNull();
  });

  it("refreshRunSummary re-fetches and updates collisionRunSummary", async () => {
    const collisionRunSummary = {
      model: "haiku",
      effort: null,
      system_prompt: "collision prompt",
      total_cost_usd: 0.002,
      total_output_tokens: 6,
      total_input_tokens: 9,
      tool_counts: [],
    };
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockResolvedValueOnce(makeChatSession({ collision_run_summary: collisionRunSummary })),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.collisionRunSummary).toBeNull();
    await act(async () => {
      await result.current.refreshRunSummary();
    });
    expect(result.current.collisionRunSummary).toEqual(collisionRunSummary);
  });

  it("refreshRunSummary re-fetches and updates projectId/repoProvision", async () => {
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockResolvedValueOnce(makeChatSession({ project_id: "proj-2", repo_provision: "error" })),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.projectId).toBeNull());
    expect(result.current.repoProvision).toBe("not_needed");
    await act(async () => {
      await result.current.refreshRunSummary();
    });
    await waitFor(() => expect(result.current.projectId).toBe("proj-2"));
    expect(result.current.repoProvision).toBe("error");
  });

  it("refreshRunSummary is a no-op when sessionId is null", async () => {
    const binding = makeBinding();
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(null, binding));
    await act(async () => {
      await result.current.refreshRunSummary();
    });
    expect(binding.get).not.toHaveBeenCalled();
  });

  it("refreshRunSummary re-fetches and updates runSummary", async () => {
    const runSummary = {
      model: "opus",
      effort: "xhigh",
      system_prompt: "prompt",
      total_cost_usd: 1,
      total_output_tokens: 10,
      total_input_tokens: 20,
      tool_counts: [],
    };
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockResolvedValueOnce(makeChatSession({ run_summary: runSummary })),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.runSummary).toEqual(makeRunSummary());
    await act(async () => {
      await result.current.refreshRunSummary();
    });
    expect(result.current.runSummary).toEqual(runSummary);
  });

  it("refreshRunSummary ignores a stale resolution after sessionId changes", async () => {
    let sidCallCount = 0;
    let resolveStale!: (v: Awaited<ReturnType<ChatSessionApi<TestBrief>["get"]>>) => void;
    const binding = makeBinding({
      get: vi.fn().mockImplementation((sid: string) => {
        if (sid === SID) {
          sidCallCount += 1;
          if (sidCallCount === 1) {
            return Promise.resolve(makeChatSession());
          }
          return new Promise((res) => {
            resolveStale = res;
          });
        }
        return Promise.resolve(makeChatSession());
      }),
    });
    const initial: { id: string | null } = { id: SID };
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useChatSessionGeneric<TestBrief>(id, binding),
      { initialProps: initial },
    );
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));

    const pending = result.current.refreshRunSummary();
    rerender({ id: SID2 });
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));

    resolveStale(makeChatSession({ run_summary: makeRunSummary({ total_cost_usd: 42 }) }));
    await act(async () => {
      await pending;
    });

    expect(result.current.runSummary).toEqual(makeRunSummary());
  });

  it("refreshRunSummary ignores a late resolution after unmount", async () => {
    let resolveRefresh!: (
      v: Awaited<ReturnType<ChatSessionApi<TestBrief>["get"]>>,
    ) => void;
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockReturnValueOnce(
          new Promise((res) => {
            resolveRefresh = res;
          }),
        ),
    });
    const { result, unmount } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    const pending = result.current.refreshRunSummary();
    unmount();
    resolveRefresh(makeChatSession({ run_summary: makeRunSummary({ total_cost_usd: 9 }) }));
    await pending;
  });

  it("refreshRunSummary swallows fetch failures and keeps the prior summary", async () => {
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockRejectedValueOnce(new Error("boom")),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.refreshRunSummary();
    });
    expect(result.current.runSummary).toEqual(makeRunSummary());
  });

  it("refreshes runSummary after an assistant_done event", async () => {
    const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
    const runSummary = {
      model: "opus",
      effort: "xhigh",
      system_prompt: "prompt",
      total_cost_usd: 2,
      total_output_tokens: 30,
      total_input_tokens: 40,
      tool_counts: [{ name: "Bash", count: 2 }],
    };
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockResolvedValueOnce(makeChatSession({ run_summary: runSummary })),
      subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        handlerHolder.handlers = handlers;
        return () => undefined;
      }),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      handlerHolder.handlers?.onEvent({
        event: "assistant_done",
        data: JSON.stringify({ content: "hello reply" }),
      });
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.runSummary).toEqual(runSummary));
  });

  it("refreshes runSummary after summarize resolves", async () => {
    const runSummary = {
      model: "opus",
      effort: "xhigh",
      system_prompt: "prompt",
      total_cost_usd: 3,
      total_output_tokens: 5,
      total_input_tokens: 6,
      tool_counts: [],
    };
    const binding = makeBinding({
      get: vi
        .fn()
        .mockResolvedValueOnce(makeChatSession())
        .mockResolvedValueOnce(makeChatSession({ run_summary: runSummary })),
    });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.summarize!();
    });
    await waitFor(() => expect(result.current.runSummary).toEqual(runSummary));
  });

  it("ignores load result after unmount", async () => {
    let resolveGet!: (v: BuildChatSessionState) => void;
    const binding = makeBinding({
      get: vi.fn().mockReturnValue(
        new Promise<BuildChatSessionState>((res) => {
          resolveGet = res;
        }),
      ),
    });
    const { unmount } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    unmount();
    resolveGet(makeChatSession());
    await new Promise((r) => setTimeout(r, 10));
  });

  it("summarize and updateBrief are undefined when the binding omits them", async () => {
    const binding = makeBinding({ summarize: undefined, putBrief: undefined });
    const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.summarize).toBeUndefined();
    expect(result.current.updateBrief).toBeUndefined();
  });

  describe("turn_stopped", () => {
    function captureSubscribe(
      binding: ChatSessionApi<TestBrief>,
    ): { handlers: ChatEventHandlers | null } {
      const holder: { handlers: ChatEventHandlers | null } = { handlers: null };
      binding.subscribe = vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        holder.handlers = handlers;
        return () => undefined;
      });
      return holder;
    }

    it("appends an interrupted assistant message and clears thinking", async () => {
      const binding = makeBinding();
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        await result.current.sendMessage("first");
      });
      expect(isTurnRunning(result.current.turn)).toBe(true);
      await act(async () => {
        handlerHolder.handlers?.onEvent({
          event: "turn_stopped",
          data: JSON.stringify({ content: "partial reply" }),
        });
        await Promise.resolve();
      });
      const last = result.current.messages.at(-1);
      expect(last?.content).toBe("partial reply");
      expect(last?.interrupted).toBe(true);
      expect(isTurnRunning(result.current.turn)).toBe(false);
      expect(result.current.toolActivity).toEqual([]);
    });

    it("uses empty content when the turn_stopped payload omits content", async () => {
      const binding = makeBinding();
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        handlerHolder.handlers?.onEvent({ event: "turn_stopped", data: "{}" });
        await Promise.resolve();
      });
      expect(result.current.messages.at(-1)?.content).toBe("");
      expect(result.current.messages.at(-1)?.interrupted).toBe(true);
    });

    it("uses empty content when the turn_stopped payload is malformed JSON", async () => {
      const binding = makeBinding();
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        handlerHolder.handlers?.onEvent({ event: "turn_stopped", data: "not-json" });
        await Promise.resolve();
      });
      expect(result.current.messages.at(-1)?.content).toBe("");
      expect(result.current.messages.at(-1)?.interrupted).toBe(true);
    });

    it("does not auto-send a queued draft (no flushQueue) but restores it via restoredDraft", async () => {
      const binding = makeBinding({
        send: vi.fn().mockResolvedValue({ status: "accepted" }),
      });
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        await result.current.sendMessage("first");
      });
      act(() => {
        void result.current.sendMessage("queued follow-up");
      });
      await waitFor(() => expect(result.current.queuedMessage).toBe("queued follow-up"));
      expect(binding.send).toHaveBeenCalledTimes(1);

      await act(async () => {
        handlerHolder.handlers?.onEvent({
          event: "turn_stopped",
          data: JSON.stringify({ content: "" }),
        });
        await Promise.resolve();
      });

      expect(binding.send).toHaveBeenCalledTimes(1);
      expect(result.current.queuedMessage).toBeNull();
      expect(result.current.restoredDraft).toBe("queued follow-up");
    });

    it("restoredDraft stays null when nothing was queued", async () => {
      const binding = makeBinding();
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        handlerHolder.handlers?.onEvent({
          event: "turn_stopped",
          data: JSON.stringify({ content: "" }),
        });
        await Promise.resolve();
      });
      expect(result.current.restoredDraft).toBeNull();
    });

    it("clearRestoredDraft resets restoredDraft to null", async () => {
      const binding = makeBinding({ send: vi.fn().mockResolvedValue({ status: "accepted" }) });
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        await result.current.sendMessage("first");
      });
      act(() => {
        void result.current.sendMessage("queued");
      });
      await waitFor(() => expect(result.current.queuedMessage).toBe("queued"));
      await act(async () => {
        handlerHolder.handlers?.onEvent({
          event: "turn_stopped",
          data: JSON.stringify({ content: "" }),
        });
        await Promise.resolve();
      });
      await waitFor(() => expect(result.current.restoredDraft).toBe("queued"));
      act(() => result.current.clearRestoredDraft());
      expect(result.current.restoredDraft).toBeNull();
    });

    it("refreshes runSummary after a turn_stopped event", async () => {
      const handlerHolder2: { handlers: ChatEventHandlers | null } = { handlers: null };
      const runSummary = {
        model: "opus",
        effort: "xhigh",
        system_prompt: "prompt",
        total_cost_usd: 1,
        total_output_tokens: 5,
        total_input_tokens: 6,
        tool_counts: [],
      };
      const binding = makeBinding({
        get: vi
          .fn()
          .mockResolvedValueOnce(makeChatSession())
          .mockResolvedValueOnce(makeChatSession({ run_summary: runSummary })),
        subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
          handlerHolder2.handlers = handlers;
          return () => undefined;
        }),
      });
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        handlerHolder2.handlers?.onEvent({
          event: "turn_stopped",
          data: JSON.stringify({ content: "" }),
        });
        await Promise.resolve();
      });
      await waitFor(() => expect(result.current.runSummary).toEqual(runSummary));
    });
  });

  describe("applyEvent exhaustiveness — every known SSE event kind", () => {
    interface EventCase {
      kind: (typeof CHAT_SSE_EVENT_KINDS)[number];
      data: string;
      assert: (r: UseGenericChatSessionResult<TestBrief>) => void;
    }

    const CASES: EventCase[] = [
      {
        kind: "assistant_done",
        data: JSON.stringify({ content: "hi", usage: "{}" }),
        assert: (r) => {
          expect(r.messages.at(-1)?.content).toBe("hi");
          expect(isTurnRunning(r.turn)).toBe(false);
        },
      },
      {
        kind: "error",
        data: JSON.stringify({ detail: "boom" }),
        assert: (r) => {
          expect(chatErrorOf(r.turn)).toBe("boom");
        },
      },
      {
        kind: "chat_event",
        data: JSON.stringify({
          event: JSON.stringify({
            type: "assistant",
            message: { content: [{ type: "tool_use", name: "Read", input: { file_path: "x.ts" } }] },
          }),
        }),
        assert: (r) => {
          expect(r.toolActivity).toHaveLength(1);
        },
      },
      {
        kind: "turn_stopped",
        data: JSON.stringify({ content: "partial" }),
        assert: (r) => {
          expect(r.messages.at(-1)?.content).toBe("partial");
          expect(r.messages.at(-1)?.interrupted).toBe(true);
          expect(isTurnRunning(r.turn)).toBe(false);
        },
      },
    ];

    it("covers every kind in CHAT_SSE_EVENT_KINDS exactly once", () => {
      expect(CASES.map((c) => c.kind).sort()).toEqual([...CHAT_SSE_EVENT_KINDS].sort());
    });

    it.each(CASES)("applies $kind via the real applyEvent handler", async ({ kind, data, assert }) => {
      const handlerHolder: { handlers: ChatEventHandlers | null } = { handlers: null };
      const binding = makeBinding({
        subscribe: vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
          handlerHolder.handlers = handlers;
          return () => undefined;
        }),
      });
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        handlerHolder.handlers?.onEvent({ event: kind, data });
        await Promise.resolve();
      });
      assert(result.current);
    });
  });

  describe("queued-message retry on 409", () => {
    function captureSubscribe(
      binding: ChatSessionApi<TestBrief>,
    ): { handlers: ChatEventHandlers | null } {
      const holder: { handlers: ChatEventHandlers | null } = { handlers: null };
      binding.subscribe = vi.fn().mockImplementation((_sid: string, handlers: ChatEventHandlers) => {
        holder.handlers = handlers;
        return () => undefined;
      });
      return holder;
    }

    it("retries on 409 then sends successfully", async () => {
      let calls = 0;
      const binding = makeBinding({
        send: vi.fn().mockImplementation(() => {
          calls += 1;
          if (calls === 1) return Promise.resolve({ status: "accepted" });
          if (calls === 2) return Promise.reject(new Error("409: locked"));
          return Promise.resolve({ status: "accepted" });
        }),
      });
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        await result.current.sendMessage("first");
      });
      act(() => {
        void result.current.sendMessage("second");
      });
      await waitFor(() => expect(result.current.queuedMessage).toBe("second"));

      act(() => {
        handlerHolder.handlers?.onEvent({
          event: "assistant_done",
          data: JSON.stringify({ content: "r" }),
        });
      });
      await waitFor(() => expect(calls).toBe(3), { timeout: 2000 });
      expect(result.current.queuedMessage).toBeNull();
    });

    it("silently drops non-409 errors from a queued send", async () => {
      let calls = 0;
      const binding = makeBinding({
        send: vi.fn().mockImplementation(() => {
          calls += 1;
          if (calls === 1) return Promise.resolve({ status: "accepted" });
          return Promise.reject(new Error("500: boom"));
        }),
      });
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        await result.current.sendMessage("first");
      });
      act(() => {
        void result.current.sendMessage("second");
      });
      await waitFor(() => expect(result.current.queuedMessage).toBe("second"));

      act(() => {
        handlerHolder.handlers?.onEvent({
          event: "assistant_done",
          data: JSON.stringify({ content: "r" }),
        });
      });
      await waitFor(() => expect(chatErrorOf(result.current.turn)).toContain("500"));
      expect(result.current.queuedMessage).toBeNull();
      expect(calls).toBe(2);
    });

    it("also flushes on an error event", async () => {
      const binding = makeBinding({
        send: vi.fn().mockResolvedValue({ status: "accepted" }),
      });
      const handlerHolder = captureSubscribe(binding);
      const { result } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        await result.current.sendMessage("first");
      });
      act(() => {
        void result.current.sendMessage("second");
      });
      await waitFor(() => expect(result.current.queuedMessage).toBe("second"));

      act(() => {
        handlerHolder.handlers?.onEvent({ event: "error", data: JSON.stringify({ detail: "fail" }) });
      });
      await waitFor(() => expect(result.current.queuedMessage).toBeNull(), { timeout: 2000 });
    });

    it("clears a pending retry timer on unmount so it never fires", async () => {
      let calls = 0;
      const binding = makeBinding({
        send: vi.fn().mockImplementation(() => {
          calls += 1;
          if (calls === 1) return Promise.resolve({ status: "accepted" });
          return Promise.reject(new Error("409: locked"));
        }),
      });
      const handlerHolder = captureSubscribe(binding);
      const { result, unmount } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        await result.current.sendMessage("first");
      });
      act(() => {
        void result.current.sendMessage("queued");
      });
      await waitFor(() => expect(result.current.queuedMessage).toBe("queued"));

      act(() => {
        handlerHolder.handlers?.onEvent({
          event: "assistant_done",
          data: JSON.stringify({ content: "reply" }),
        });
      });
      await waitFor(() => expect(result.current.queuedMessage).toBe("queued"));

      const callsAtUnmount = calls;
      unmount();
      await new Promise((r) => setTimeout(r, 350));
      expect(calls).toBe(callsAtUnmount);
    });

    it("does not schedule a new retry when a 409 rejection resolves after unmount", async () => {
      let calls = 0;
      let rejectSecondSend!: (err: Error) => void;
      const binding = makeBinding({
        send: vi.fn().mockImplementation(() => {
          calls += 1;
          if (calls === 1) return Promise.resolve({ status: "accepted" });
          return new Promise((_resolve, reject) => {
            rejectSecondSend = reject;
          });
        }),
      });
      const handlerHolder = captureSubscribe(binding);
      const { result, unmount } = renderHook(() => useChatSessionGeneric<TestBrief>(SID, binding));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await act(async () => {
        await result.current.sendMessage("first");
      });
      act(() => {
        void result.current.sendMessage("queued");
      });
      await waitFor(() => expect(result.current.queuedMessage).toBe("queued"));

      act(() => {
        handlerHolder.handlers?.onEvent({
          event: "assistant_done",
          data: JSON.stringify({ content: "reply" }),
        });
      });
      await waitFor(() => expect(calls).toBe(2));

      unmount();
      rejectSecondSend(new Error("409: locked"));
      await new Promise((r) => setTimeout(r, 350));
      expect(calls).toBe(2);
    });
  });
});
