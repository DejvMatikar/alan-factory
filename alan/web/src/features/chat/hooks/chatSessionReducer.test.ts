import { describe, expect, it } from "vitest";

import {
  actionOfSseEvent,
  archivedTaskIdOf,
  chatErrorOf,
  chatSessionReducer,
  isTurnRunning,
  makeInitialChatState,
  type ChatAction,
  type ChatReducerState,
  type ChatSessionShape,
} from "./chatSessionReducer";

interface TestBrief {
  content: string;
}

function baseState(overrides: Partial<ChatReducerState<TestBrief>> = {}): ChatReducerState<TestBrief> {
  return { ...makeInitialChatState<TestBrief>(), ...overrides };
}

function readySession(overrides: Partial<ChatSessionShape<TestBrief>> = {}): ChatSessionShape<TestBrief> {
  return {
    messages: [],
    in_flight_turn: null,
    brief: null,
    run_summary: { model: "opus", effort: "xhigh", system_prompt: "p" },
    repo_provision: "not_needed",
    ...overrides,
  };
}

function apply(
  state: ChatReducerState<TestBrief>,
  action: ChatAction<TestBrief>,
): ChatReducerState<TestBrief> {
  return chatSessionReducer(state, action);
}

describe("makeInitialChatState", () => {
  it("starts loading, idle, and empty", () => {
    const s = makeInitialChatState<TestBrief>();
    expect(s.session).toEqual({ kind: "loading" });
    expect(s.turn).toEqual({ kind: "idle" });
    expect(s.messages).toEqual([]);
    expect(s.brief).toBeNull();
    expect(s.toolActivity).toEqual([]);
    expect(s.queuedMessage).toBeNull();
    expect(s.restoredDraft).toBeNull();
  });
});

describe("chatSessionReducer", () => {
  it("load_reset returns a fresh initial state regardless of prior state", () => {
    const dirty = baseState({
      session: { kind: "ready" },
      turn: { kind: "failed", error: "boom" },
      messages: [{ role: "user", content: "hi", ts: "t", interrupted: false }],
      queuedMessage: "q",
    });
    expect(apply(dirty, { type: "load_reset" })).toEqual(makeInitialChatState<TestBrief>());
  });

  it("session_loaded moves to ready, idle when no in-flight turn", () => {
    const next = apply(baseState(), {
      type: "session_loaded",
      session: readySession({ messages: [{ role: "user", content: "hi", ts: "t", interrupted: false }] }),
    });
    expect(next.session).toEqual({ kind: "ready" });
    expect(next.turn).toEqual({ kind: "idle" });
    expect(next.messages).toEqual([{ role: "user", content: "hi", ts: "t", interrupted: false }]);
    expect(next.brief).toBeNull();
  });

  it("session_loaded starts a running turn when an in-flight turn is present", () => {
    const next = apply(baseState(), {
      type: "session_loaded",
      session: readySession({ in_flight_turn: { started_at: "t", user_text: "hi" } }),
    });
    expect(next.turn).toEqual({ kind: "running", notice: null });
  });

  it("session_loaded defaults brief to null when the session omits it", () => {
    const next = apply(baseState(), { type: "session_loaded", session: readySession() });
    expect(next.brief).toBeNull();
  });

  it("session_loaded carries a brief through when present", () => {
    const next = apply(baseState(), {
      type: "session_loaded",
      session: readySession({ brief: { content: "b" } }),
    });
    expect(next.brief).toEqual({ content: "b" });
  });

  it("session_load_failed moves to load_failed with a failed turn carrying the message", () => {
    const next = apply(baseState(), { type: "session_load_failed", error: "500: boom" });
    expect(next.session).toEqual({ kind: "load_failed" });
    expect(next.turn).toEqual({ kind: "failed", error: "500: boom" });
  });

  it("session_archived moves to archived carrying the workspace id", () => {
    const next = apply(baseState(), { type: "session_archived", archivedTaskId: "ws-1" });
    expect(next.session).toEqual({ kind: "archived", archivedTaskId: "ws-1" });
  });

  it("send_started appends the user message, clears tool activity, and starts a running turn", () => {
    const start = baseState({ toolActivity: [{ id: "tool-1", label: "Read: a" }] });
    const next = apply(start, { type: "send_started", content: "hi", ts: "t1" });
    expect(next.messages).toEqual([{ role: "user", content: "hi", ts: "t1", interrupted: false }]);
    expect(next.toolActivity).toEqual([]);
    expect(next.turn).toEqual({ kind: "running", notice: null });
  });

  it("send_failed fails the turn with the given error", () => {
    const start = baseState({ turn: { kind: "running", notice: null } });
    const next = apply(start, { type: "send_failed", error: "409: locked" });
    expect(next.turn).toEqual({ kind: "failed", error: "409: locked" });
  });

  it("message_queued sets the queued message", () => {
    const next = apply(baseState(), { type: "message_queued", content: "later" });
    expect(next.queuedMessage).toBe("later");
  });

  it("queue_cancelled clears the queued message and any active flush", () => {
    const start = baseState({ queuedMessage: "later", flush: { id: 1, content: "later", attempt: 1 } });
    const next = apply(start, { type: "queue_cancelled" });
    expect(next.queuedMessage).toBeNull();
    expect(next.flush).toBeNull();
  });

  it("flush_started appends the user message once, clears the queue, and starts a running turn", () => {
    const start = baseState({ queuedMessage: "later" });
    const next = apply(start, { type: "flush_started", content: "later", ts: "t2" });
    expect(next.messages).toEqual([{ role: "user", content: "later", ts: "t2", interrupted: false }]);
    expect(next.queuedMessage).toBeNull();
    expect(next.turn).toEqual({ kind: "running", notice: null });
    expect(next.flush).toEqual({ id: 1, content: "later", attempt: 0 });
  });

  it("flush_started assigns a strictly increasing id from a per-load counter, never wall-clock time", () => {
    const first = apply(baseState(), { type: "flush_started", content: "a", ts: "t1" });
    const second = apply(
      { ...first, flush: null, queuedMessage: "b" },
      { type: "flush_started", content: "b", ts: "t2" },
    );
    expect(first.flush?.id).toBe(1);
    expect(second.flush?.id).toBe(2);
  });

  it("flush_retry_scheduled restores the queued indicator and idles the turn, bumping attempt", () => {
    const start = baseState({
      turn: { kind: "running", notice: null },
      flush: { id: 7, content: "later", attempt: 0 },
    });
    const next = apply(start, { type: "flush_retry_scheduled" });
    expect(next.turn).toEqual({ kind: "idle" });
    expect(next.queuedMessage).toBe("later");
    expect(next.flush).toEqual({ id: 7, content: "later", attempt: 1 });
  });

  it("flush_retry_scheduled is a no-op without an active flush", () => {
    const start = baseState();
    expect(apply(start, { type: "flush_retry_scheduled" })).toEqual(start);
  });

  it("flush_retry_started resumes the running turn and clears the queued indicator", () => {
    const start = baseState({
      queuedMessage: "later",
      flush: { id: 7, content: "later", attempt: 1 },
    });
    const next = apply(start, { type: "flush_retry_started" });
    expect(next.turn).toEqual({ kind: "running", notice: null });
    expect(next.queuedMessage).toBeNull();
  });

  it("flush_retry_started is a no-op without an active flush", () => {
    const start = baseState();
    expect(apply(start, { type: "flush_retry_started" })).toEqual(start);
  });

  it("flush_failed fails the turn and clears the flush", () => {
    const start = baseState({ flush: { id: 7, content: "later", attempt: 3 } });
    const next = apply(start, { type: "flush_failed", error: "500: boom" });
    expect(next.turn).toEqual({ kind: "failed", error: "500: boom" });
    expect(next.flush).toBeNull();
  });

  it("flush_succeeded clears the flush without touching the turn", () => {
    const start = baseState({
      turn: { kind: "running", notice: null },
      flush: { id: 7, content: "later", attempt: 1 },
    });
    const next = apply(start, { type: "flush_succeeded" });
    expect(next.flush).toBeNull();
    expect(next.turn).toEqual({ kind: "running", notice: null });
  });

  it("assistant_done appends a non-interrupted assistant message, clears tool activity, and idles the turn", () => {
    const start = baseState({
      turn: { kind: "running", notice: null },
      toolActivity: [{ id: "tool-1", label: "Read: a" }],
    });
    const next = apply(start, { type: "assistant_done", content: "reply", ts: "t3" });
    expect(next.messages).toEqual([{ role: "assistant", content: "reply", ts: "t3", interrupted: false }]);
    expect(next.toolActivity).toEqual([]);
    expect(next.turn).toEqual({ kind: "idle" });
  });

  it("turn_stopped appends an interrupted assistant message and idles the turn", () => {
    const start = baseState({ turn: { kind: "running", notice: null } });
    const next = apply(start, { type: "turn_stopped", content: "partial", ts: "t4" });
    expect(next.messages).toEqual([{ role: "assistant", content: "partial", ts: "t4", interrupted: true }]);
    expect(next.turn).toEqual({ kind: "idle" });
  });

  it("turn_stopped restores a queued draft and clears the queue, without flushing", () => {
    const start = baseState({ turn: { kind: "running", notice: null }, queuedMessage: "follow-up" });
    const next = apply(start, { type: "turn_stopped", content: "", ts: "t5" });
    expect(next.queuedMessage).toBeNull();
    expect(next.restoredDraft).toBe("follow-up");
  });

  it("turn_stopped leaves restoredDraft untouched when nothing was queued", () => {
    const start = baseState({ turn: { kind: "running", notice: null } });
    const next = apply(start, { type: "turn_stopped", content: "", ts: "t6" });
    expect(next.restoredDraft).toBeNull();
  });

  it("sse_error fails the turn with the parsed detail", () => {
    const start = baseState({ turn: { kind: "running", notice: null } });
    const next = apply(start, { type: "sse_error", detail: "boom" });
    expect(next.turn).toEqual({ kind: "failed", error: "boom" });
  });

  it("turn_error_attached leaves a running turn running, carrying the message as a notice", () => {
    const start = baseState({ turn: { kind: "running", notice: null } });
    const next = apply(start, { type: "turn_error_attached", detail: "stream error" });
    expect(next.turn).toEqual({ kind: "running", notice: "stream error" });
  });

  it("turn_error_attached fails an idle turn", () => {
    const next = apply(baseState(), { type: "turn_error_attached", detail: "stream error" });
    expect(next.turn).toEqual({ kind: "failed", error: "stream error" });
  });

  it("turn_error_cleared idles a failed turn", () => {
    const start = baseState({ turn: { kind: "failed", error: "boom" } });
    expect(apply(start, { type: "turn_error_cleared" }).turn).toEqual({ kind: "idle" });
  });

  it("turn_error_cleared drops the notice from a running turn", () => {
    const start = baseState({ turn: { kind: "running", notice: "stream error" } });
    expect(apply(start, { type: "turn_error_cleared" }).turn).toEqual({ kind: "running", notice: null });
  });

  it("turn_error_cleared leaves an idle turn idle", () => {
    const start = baseState();
    expect(apply(start, { type: "turn_error_cleared" }).turn).toEqual({ kind: "idle" });
  });

  it("chat_event appends a tool activity entry with an incrementing id", () => {
    const start = baseState({ toolSeq: 2 });
    const next = apply(start, { type: "chat_event", label: "Read: a.ts" });
    expect(next.toolActivity).toEqual([{ id: "tool-3", label: "Read: a.ts" }]);
    expect(next.toolSeq).toBe(3);
  });

  it("chat_event is a no-op when the label cannot be parsed", () => {
    const start = baseState();
    expect(apply(start, { type: "chat_event", label: null })).toEqual(start);
  });

  it("brief_set replaces the brief", () => {
    const next = apply(baseState(), { type: "brief_set", brief: { content: "new" } });
    expect(next.brief).toEqual({ content: "new" });
  });

  it("restored_draft_cleared clears the restored draft", () => {
    const start = baseState({ restoredDraft: "draft" });
    expect(apply(start, { type: "restored_draft_cleared" }).restoredDraft).toBeNull();
  });

  it("session_cleared resets to a fresh ready state", () => {
    const dirty = baseState({
      session: { kind: "load_failed" },
      turn: { kind: "failed", error: "boom" },
      messages: [{ role: "user", content: "hi", ts: "t", interrupted: false }],
      queuedMessage: "q",
    });
    const next = apply(dirty, { type: "session_cleared" });
    expect(next.session).toEqual({ kind: "ready" });
    expect(next.turn).toEqual({ kind: "idle" });
    expect(next.messages).toEqual([]);
    expect(next.queuedMessage).toBeNull();
  });
});

describe("isTurnRunning", () => {
  it("is true only for a running turn", () => {
    expect(isTurnRunning({ kind: "idle" })).toBe(false);
    expect(isTurnRunning({ kind: "running", notice: null })).toBe(true);
    expect(isTurnRunning({ kind: "failed", error: "x" })).toBe(false);
  });
});

describe("chatErrorOf", () => {
  it("reads the error off a failed turn", () => {
    expect(chatErrorOf({ kind: "failed", error: "boom" })).toBe("boom");
  });

  it("reads the notice off a running turn", () => {
    expect(chatErrorOf({ kind: "running", notice: "stream error" })).toBe("stream error");
  });

  it("is null for a running turn with no notice", () => {
    expect(chatErrorOf({ kind: "running", notice: null })).toBeNull();
  });

  it("is null for an idle turn", () => {
    expect(chatErrorOf({ kind: "idle" })).toBeNull();
  });
});

describe("archivedTaskIdOf", () => {
  it("reads the workspace id off an archived session", () => {
    expect(archivedTaskIdOf({ kind: "archived", archivedTaskId: "ws-1" })).toBe("ws-1");
  });

  it("is null for loading, ready, and load_failed sessions", () => {
    expect(archivedTaskIdOf({ kind: "loading" })).toBeNull();
    expect(archivedTaskIdOf({ kind: "ready" })).toBeNull();
    expect(archivedTaskIdOf({ kind: "load_failed" })).toBeNull();
  });
});

describe("actionOfSseEvent", () => {
  it("maps assistant_done to a content-and-timestamp action", () => {
    const action = actionOfSseEvent<TestBrief>({
      event: "assistant_done",
      data: JSON.stringify({ content: "hi" }),
    });
    expect(action).toMatchObject({ type: "assistant_done", content: "hi" });
    expect(typeof (action as { ts: string }).ts).toBe("string");
  });

  it("maps turn_stopped through the same content parse as assistant_done", () => {
    const data = JSON.stringify({ content: "partial" });
    const assistantDone = actionOfSseEvent<TestBrief>({ event: "assistant_done", data });
    const turnStopped = actionOfSseEvent<TestBrief>({ event: "turn_stopped", data });
    expect((assistantDone as { content: string }).content).toBe(
      (turnStopped as { content: string }).content,
    );
  });

  it("falls back to empty content when the payload is malformed JSON", () => {
    const action = actionOfSseEvent<TestBrief>({ event: "assistant_done", data: "not-json" });
    expect(action).toMatchObject({ type: "assistant_done", content: "" });
  });

  it("maps error to a parsed detail", () => {
    const action = actionOfSseEvent<TestBrief>({
      event: "error",
      data: JSON.stringify({ detail: "boom" }),
    });
    expect(action).toEqual({ type: "sse_error", detail: "boom" });
  });

  it("falls back to the raw payload when the error detail is not a string", () => {
    const raw = JSON.stringify({ detail: 123 });
    const action = actionOfSseEvent<TestBrief>({ event: "error", data: raw });
    expect(action).toEqual({ type: "sse_error", detail: raw });
  });

  it("falls back to the raw payload when the error data isn't valid JSON", () => {
    const action = actionOfSseEvent<TestBrief>({ event: "error", data: "raw text" });
    expect(action).toEqual({ type: "sse_error", detail: "raw text" });
  });

  it("maps chat_event to a parsed tool-activity label", () => {
    const inner = JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "tool_use", name: "Read", input: { file_path: "a.ts" } }] },
    });
    const action = actionOfSseEvent<TestBrief>({
      event: "chat_event",
      data: JSON.stringify({ event: inner }),
    });
    expect(action).toEqual({ type: "chat_event", label: "Read: a.ts" });
  });

  it("maps an unparseable chat_event to a null label", () => {
    const action = actionOfSseEvent<TestBrief>({ event: "chat_event", data: "not-json" });
    expect(action).toEqual({ type: "chat_event", label: null });
  });

  it("returns null for an unknown event kind", () => {
    expect(actionOfSseEvent<TestBrief>({ event: "unknown", data: "{}" })).toBeNull();
  });
});
