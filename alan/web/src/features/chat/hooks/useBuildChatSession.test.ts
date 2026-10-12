import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BuildChatSessionState } from "../../../api";
import { useInvalidateEnginesList } from "../../../api/queries";
import { chatErrorOf, isTurnRunning } from "./chatSessionReducer";
import { makeChatMessage, makeChatSession as sessionResp } from "../../../test/chatFixtures";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  gatedEngineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { useEngines } from "../../../hooks/useEngines";
import { useBuildChatSession } from "./useBuildChatSession";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const BUILD_CHAT_PATH = "/api/build-chat/sessions";
const OWN_KEY = "acme/sprocket";

function useSessionWithEngines(sessionId: string | null) {
  const chat = useBuildChatSession(sessionId);
  const { loaded, defaultEngineKey } = useEngines();
  return { chat, enginesLoaded: loaded, defaultEngineKey, refreshEngines: useInvalidateEnginesList() };
}

interface CreateBody {
  engine_key?: string;
  project_id?: string;
}

function serveSessionLifecycle(overrides: Partial<BuildChatSessionState> = {}) {
  const created: CreateBody[] = [];
  server.use(
    http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp(overrides))),
    http.delete(`${BUILD_CHAT_PATH}/${SID}`, () => new HttpResponse(null, { status: 204 })),
    http.post(BUILD_CHAT_PATH, async ({ request }) => {
      created.push((await request.json()) as CreateBody);
      return HttpResponse.json({ session_id: "cccccccc-cccc-cccc-cccc-cccccccccccc" }, { status: 201 });
    }),
  );
  return created;
}

describe("useBuildChatSession", () => {
  it("returns loading state when sessionId is null", () => {
    const { result } = renderHook(() => useBuildChatSession(null));
    expect(result.current.session.kind).toBe("loading");
    expect(result.current.messages).toEqual([]);
    expect(result.current.brief).toBeNull();
  });

  it("loads session and transitions to ready", async () => {
    server.use(http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())));
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.messages).toEqual([]);
  });

  it("sets error status when session load fails", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("load_failed"));
    expect(chatErrorOf(result.current.turn)).toBeTruthy();
  });

  it("projectId and engineKey are null by default", async () => {
    server.use(http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())));
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.projectId).toBeNull());
    expect(result.current.engineKey).toBeNull();
  });

  it("surfaces projectId and engineKey from the session's own state", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(
          sessionResp({ project_id: "proj-a", engine_key: "acme/sprocket" }),
        ),
      ),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.projectId).toBe("proj-a"));
    expect(result.current.engineKey).toBe("acme/sprocket");
  });

  it("exposes repo_provision from the session GET response", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(sessionResp({ repo_provision: "ready" })),
      ),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.repoProvision).toBe("ready");
  });

  it("projectId and engineKey are null while sessionId is null", () => {
    const { result } = renderHook(() => useBuildChatSession(null));
    expect(result.current.projectId).toBeNull();
    expect(result.current.engineKey).toBeNull();
  });

  it("resets projectId and engineKey when sessionId becomes null", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(sessionResp({ project_id: "proj-a", engine_key: "acme/sprocket" })),
      ),
    );
    const initialProps: { sid: string | null } = { sid: SID };
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useBuildChatSession(sid),
      { initialProps },
    );
    await waitFor(() => expect(result.current.projectId).toBe("proj-a"));
    rerender({ sid: null });
    expect(result.current.projectId).toBeNull();
    expect(result.current.engineKey).toBeNull();
  });

  it("ignores a stale binding fetch that resolves after sessionId changes", async () => {
    const NEW_SID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
    let resolveFirst!: (v: BuildChatSessionState) => void;
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(sessionResp({ project_id: "proj-a", engine_key: "acme/sprocket" })),
      ),
      http.get(`${BUILD_CHAT_PATH}/${NEW_SID}`, () =>
        HttpResponse.json(
          sessionResp({ session_id: NEW_SID, project_id: "proj-b", engine_key: "acme/other" }),
        ),
      ),
    );
    const original = (await import("../../../api")).api.getBuildChatSession;
    let call = 0;
    (await import("../../../api")).api.getBuildChatSession = (sid: string) => {
      call += 1;
      if (call === 1) {
        return new Promise<BuildChatSessionState>((resolve) => {
          resolveFirst = resolve;
        });
      }
      return original(sid);
    };
    try {
      const initialProps: { sid: string } = { sid: SID };
      const { result, rerender } = renderHook(
        ({ sid }: { sid: string }) => useBuildChatSession(sid),
        { initialProps },
      );
      rerender({ sid: NEW_SID });
      await waitFor(() => expect(result.current.projectId).toBe("proj-b"));
      act(() => {
        resolveFirst(sessionResp({ project_id: "proj-a", engine_key: "acme/sprocket" }));
      });
      await Promise.resolve();
      expect(result.current.projectId).toBe("proj-b");
      expect(result.current.engineKey).toBe("acme/other");
    } finally {
      (await import("../../../api")).api.getBuildChatSession = original;
    }
  });

  it("leaves the binding empty when the session fetch fails", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("load_failed"));
    expect(result.current.projectId).toBeNull();
    expect(result.current.engineKey).toBeNull();
  });

  it("issues exactly one GET for the session on mount", async () => {
    let calls = 0;
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => {
        calls += 1;
        return HttpResponse.json(sessionResp({ project_id: "proj-a", engine_key: "acme/sprocket" }));
      }),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.engineKey).toBe("acme/sprocket"));
    expect(calls).toBe(1);
  });

  it("retains engineKey and projectId across a failed background refresh after a prior success", async () => {
    let calls = 0;
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json(sessionResp({ project_id: "proj-a", engine_key: "acme/sprocket" }));
        }
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.engineKey).toBe("acme/sprocket"));
    await act(async () => {
      await result.current.refreshRunSummary();
    });
    expect(result.current.engineKey).toBe("acme/sprocket");
    expect(result.current.projectId).toBe("proj-a");
  });

  it("loads existing messages and brief", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(
          sessionResp({
            messages: [makeChatMessage({ role: "user", content: "hi", ts: "2026-05-01T00:00:01Z" })],
            brief: { content: "my brief", updated_at: "2026-05-01T00:00:02Z" },
          }),
        ),
      ),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.brief?.content).toBe("my brief");
  });

  it("starts a running turn for in-flight sessions", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(
          sessionResp({ in_flight_turn: { started_at: "2026-05-01T00:00:01Z", user_text: "q" } }),
        ),
      ),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(isTurnRunning(result.current.turn)).toBe(true);
  });

  it("resets to INITIAL_STATE when sessionId becomes null", async () => {
    server.use(http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())));
    const initialProps: { sid: string | null } = { sid: SID };
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useBuildChatSession(sid),
      { initialProps },
    );
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    rerender({ sid: null });
    expect(result.current.messages).toEqual([]);
    expect(result.current.session.kind).toBe("loading");
  });

  it("sendMessage rejects with 409 when locked", async () => {
    server.use(http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())));
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/messages`, () =>
        HttpResponse.json({ detail: "locked" }, { status: 409 }),
      ),
    );
    await act(async () => {
      await expect(result.current.sendMessage("first")).rejects.toThrow("409");
    });
  });

  it("sendMessage throws when sessionId is null", async () => {
    const { result } = renderHook(() => useBuildChatSession(null));
    await act(async () => {
      await expect(result.current.sendMessage("hi")).rejects.toThrow("no build-chat session");
    });
  });

  it("interrupt does nothing when sessionId is null", async () => {
    const { result } = renderHook(() => useBuildChatSession(null));
    await act(async () => {
      await expect(result.current.interrupt()).resolves.toBeUndefined();
    });
  });

  it("interrupt calls the interrupt endpoint", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => new HttpResponse(null, { status: 204 })),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => { await result.current.interrupt(); });
  });

  it("interrupt surfaces error", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await expect(result.current.interrupt()).rejects.toThrow();
    });
    expect(chatErrorOf(result.current.turn)).toBeTruthy();
  });

  it("summarize throws when sessionId is null", async () => {
    const { result } = renderHook(() => useBuildChatSession(null));
    await act(async () => {
      await expect(result.current.summarize()).rejects.toThrow("no build-chat session");
    });
  });

  it("summarize calls the summarize endpoint and updates brief", async () => {
    const briefResp = { content: "brief text", updated_at: "2026-05-01T01:00:00Z" };
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BUILD_CHAT_PATH}/${SID}/summarize`, () => HttpResponse.json(briefResp)),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => { await result.current.summarize(); });
    expect(result.current.brief?.content).toBe("brief text");
  });

  it("updateBrief throws when sessionId is null", async () => {
    const { result } = renderHook(() => useBuildChatSession(null));
    await act(async () => {
      await expect(result.current.updateBrief("x")).rejects.toThrow("no build-chat session");
    });
  });

  it("updateBrief calls PUT /brief and updates state", async () => {
    const briefResp = { content: "edited", updated_at: "2026-05-01T02:00:00Z" };
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, () => HttpResponse.json(briefResp)),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => { await result.current.updateBrief("edited"); });
    expect(result.current.brief?.content).toBe("edited");
  });

  it("clear creates a new session and updates id", async () => {
    const NEW_SID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.delete(`${BUILD_CHAT_PATH}/${SID}`, () => new HttpResponse(null, { status: 204 })),
      http.post(BUILD_CHAT_PATH, () =>
        HttpResponse.json({ session_id: NEW_SID }, { status: 201 }),
      ),
    );
    const { result } = renderHook(() => useSessionWithEngines(SID));
    await waitFor(() => expect(result.current.chat.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
    let newSid: string | undefined;
    await act(async () => { newSid = await result.current.chat.clear(); });
    expect(newSid).toBe(NEW_SID);
    expect(result.current.chat.messages).toEqual([]);
  });

  it("clear works even with null sessionId", async () => {
    const NEW_SID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
    server.use(
      http.post(BUILD_CHAT_PATH, () =>
        HttpResponse.json({ session_id: NEW_SID }, { status: 201 }),
      ),
    );
    const { result } = renderHook(() => useSessionWithEngines(null));
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
    let newSid: string | undefined;
    await act(async () => { newSid = await result.current.chat.clear(); });
    expect(newSid).toBe(NEW_SID);
  });

  it("clear creates a new session using the session's own engineKey and projectId", async () => {
    const NEW_SID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
    let capturedBody: { engine_key?: string; project_id?: string } | null = null;
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(sessionResp({ project_id: "proj-a", engine_key: "acme/sprocket" })),
      ),
      http.delete(`${BUILD_CHAT_PATH}/${SID}`, () => new HttpResponse(null, { status: 204 })),
      http.post(BUILD_CHAT_PATH, async ({ request }) => {
        capturedBody = (await request.json()) as { engine_key?: string; project_id?: string };
        return HttpResponse.json({ session_id: NEW_SID }, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.engineKey).toBe("acme/sprocket"));
    await act(async () => { await result.current.clear(); });
    expect(capturedBody).toEqual({ engine_key: "acme/sprocket", project_id: "proj-a" });
  });

  it("ignores a binding fetch failure that resolves after unmount", async () => {
    const rejecters: ((reason: unknown) => void)[] = [];
    server.use(
      http.get(
        `${BUILD_CHAT_PATH}/${SID}`,
        () =>
          new Promise<Response>((_resolve, reject) => {
            rejecters.push(reject);
          }),
      ),
    );
    const { unmount } = renderHook(() => useBuildChatSession(SID));
    await new Promise((r) => setTimeout(r, 5));
    unmount();
    rejecters.forEach((reject) => reject(new Error("late binding failure")));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("clear rejects instead of defaulting when the session's own binding fetch failed", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.delete(`${BUILD_CHAT_PATH}/${SID}`, () => new HttpResponse(null, { status: 204 })),
    );
    let createCalls = 0;
    server.use(
      http.post(BUILD_CHAT_PATH, () => {
        createCalls += 1;
        return HttpResponse.json({ session_id: "should-not-be-used" }, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("load_failed"));
    await act(async () => {
      await expect(result.current.clear()).rejects.toThrow("repository unresolved for this session");
    });
    expect(createCalls).toBe(0);
  });

  it("getBuildChatBrief calls GET /sessions/{id}/brief", async () => {
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}/brief`, () =>
        HttpResponse.json({ content: "brief", updated_at: "2026-05-01T01:00:00Z" }),
      ),
    );
    const { api } = await import("../../../api");
    const brief = await api.getBuildChatBrief(SID);
    expect(brief?.content).toBe("brief");
  });

  it("deleteBuildChatSession throws on error response", async () => {
    server.use(
      http.delete(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    const { api } = await import("../../../api");
    await expect(api.deleteBuildChatSession(SID)).rejects.toThrow("404");
  });

  it("sendMessage queues and flushes after assistant_done", async () => {
    const sse: { push: (chunk: string) => void } = {
      push: () => undefined,
    };
    const sent: string[] = [];
    let sseCtl: ReadableStreamDefaultController<Uint8Array> | null = null;
    const enc = new TextEncoder();
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(
          sessionResp({ in_flight_turn: { started_at: "t", user_text: "first" } }),
        ),
      ),
      http.get(`${BUILD_CHAT_PATH}/${SID}/events`, () =>
        new HttpResponse(
          new ReadableStream<Uint8Array>({ start(ctl) { sseCtl = ctl; } }),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
      http.post(`${BUILD_CHAT_PATH}/${SID}/messages`, async ({ request }) => {
        const body = await request.json() as { content: string };
        sent.push(body.content);
        return HttpResponse.json({ status: "accepted" }, { status: 202 });
      }),
    );
    sse.push = (chunk: string) => sseCtl?.enqueue(enc.encode(chunk));
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(isTurnRunning(result.current.turn)).toBe(true));
    await act(async () => { await result.current.sendMessage("second"); });
    expect(result.current.queuedMessage).toBe("second");
    act(() => {
      sse.push(`event: assistant_done\ndata: ${JSON.stringify({ content: "reply", usage: "{}" })}\n\n`);
    });
    await waitFor(() => expect(sent).toContain("second"), { timeout: 3000 });
  });

  it("cancelQueued clears the queued message", async () => {
    server.use(http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())));
    const { result } = renderHook(() => useBuildChatSession(SID));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    act(() => result.current.cancelQueued());
    expect(result.current.queuedMessage).toBeNull();
  });

  describe("SSE event handling", () => {
    const enc = new TextEncoder();

    function stubPushableSse(): { push: (chunk: string) => void } {
      const ctl: { c: ReadableStreamDefaultController<Uint8Array> | null } = { c: null };
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
        http.get(`${BUILD_CHAT_PATH}/${SID}/events`, () =>
          new HttpResponse(
            new ReadableStream<Uint8Array>({ start(ctrl) { ctl.c = ctrl; } }),
            { headers: { "Content-Type": "text/event-stream" } },
          ),
        ),
      );
      return { push: (chunk: string) => ctl.c?.enqueue(enc.encode(chunk)) };
    }

    afterEach(() => { vi.clearAllMocks(); });

    it("handles assistant_done event and adds assistant message", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useBuildChatSession(SID));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => {
        sse.push(`event: assistant_done\ndata: ${JSON.stringify({ content: "reply", usage: "{}" })}\n\n`);
      });
      await waitFor(() => expect(result.current.messages).toHaveLength(1));
      expect(result.current.messages[0]?.content).toBe("reply");
      expect(isTurnRunning(result.current.turn)).toBe(false);
    });

    it("handles assistant_done with malformed data (JSON parse fails)", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useBuildChatSession(SID));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => { sse.push("event: assistant_done\ndata: not-json\n\n"); });
      await waitFor(() => expect(result.current.messages).toHaveLength(1));
      expect(result.current.messages[0]?.content).toBe("");
    });

    it("handles assistant_done without content field (uses empty string fallback)", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useBuildChatSession(SID));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => { sse.push(`event: assistant_done\ndata: ${JSON.stringify({ usage: "{}" })}\n\n`); });
      await waitFor(() => expect(result.current.messages).toHaveLength(1));
      expect(result.current.messages[0]?.content).toBe("");
    });

    it("ignores unknown SSE event types", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useBuildChatSession(SID));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => { sse.push("event: unknown_event\ndata: {}\n\n"); });
      expect(result.current.messages).toHaveLength(0);
      expect(result.current.toolActivity).toHaveLength(0);
    });

    it("handles error event", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useBuildChatSession(SID));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => {
        sse.push(`event: error\ndata: ${JSON.stringify({ detail: "failed" })}\n\n`);
      });
      await waitFor(() => expect(chatErrorOf(result.current.turn)).toBe("failed"));
    });

    it("handles chat_event (tool activity)", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useBuildChatSession(SID));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      const chatEvent = JSON.stringify({
        event: JSON.stringify({
          type: "assistant",
          message: {
            content: [{ type: "tool_use", name: "Bash", input: { command: "ls" } }],
          },
        }),
      });
      act(() => { sse.push(`event: chat_event\ndata: ${chatEvent}\n\n`); });
      await waitFor(() => expect(result.current.toolActivity).toHaveLength(1));
    });

    it("sets error on stream error", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
        http.get(`${BUILD_CHAT_PATH}/${SID}/events`, () =>
          HttpResponse.json({ detail: "stream error" }, { status: 500 }),
        ),
      );
      const { result } = renderHook(() => useBuildChatSession(SID));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await waitFor(() => expect(chatErrorOf(result.current.turn)).toBeTruthy());
    });

    it("handles turn_stopped: appends an interrupted assistant message and clears thinking", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useBuildChatSession(SID));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => {
        sse.push(`event: turn_stopped\ndata: ${JSON.stringify({ content: "partial" })}\n\n`);
      });
      await waitFor(() => expect(result.current.messages).toHaveLength(1));
      expect(result.current.messages[0]?.content).toBe("partial");
      expect(result.current.messages[0]?.interrupted).toBe(true);
      expect(isTurnRunning(result.current.turn)).toBe(false);
      expect(result.current.restoredDraft).toBeNull();
    });
  });
});

describe("useBuildChatSession — default repository fallback", () => {
  it("clear rejects with 'default repository not loaded' and makes no create request before the engine list has loaded", async () => {
    const created = serveSessionLifecycle();
    const listing = gatedEngineListingHandler({
      engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
      defaultKey: LISTED_DEFAULT_KEY,
    });
    server.use(listing.handler);
    const { result } = renderHook(() => useSessionWithEngines(SID));
    await waitFor(() => expect(result.current.chat.session.kind).toBe("ready"));
    expect(result.current.enginesLoaded).toBe(false);
    await act(async () => {
      await expect(result.current.chat.clear()).rejects.toThrow("default repository not loaded");
    });
    expect(created).toEqual([]);
    act(() => { listing.release(); });
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
  });

  it("clear with no session bound rejects with 'default repository not loaded' and makes no create request before the engine list has loaded", async () => {
    const created = serveSessionLifecycle();
    const listing = gatedEngineListingHandler({ engines: [], defaultKey: LISTED_DEFAULT_KEY });
    server.use(listing.handler);
    const { result } = renderHook(() => useSessionWithEngines(null));
    await act(async () => {
      await expect(result.current.chat.clear()).rejects.toThrow("default repository not loaded");
    });
    expect(created).toEqual([]);
    act(() => { listing.release(); });
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
  });

  it("clear reports the unresolved repository, not the unloaded default, when the binding fetch failed before the list loaded", async () => {
    const created = serveSessionLifecycle();
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const listing = gatedEngineListingHandler({ engines: [], defaultKey: LISTED_DEFAULT_KEY });
    server.use(listing.handler);
    const { result } = renderHook(() => useSessionWithEngines(SID));
    await waitFor(() => expect(result.current.chat.session.kind).toBe("load_failed"));
    expect(result.current.enginesLoaded).toBe(false);
    await act(async () => {
      await expect(result.current.chat.clear()).rejects.toThrow(
        "repository unresolved for this session",
      );
    });
    expect(created).toEqual([]);
    act(() => { listing.release(); });
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
  });

  it.each(DEFAULT_KEY_STATES)(
    "clear starts the replacement session on the resolved default once the list has loaded (%s)",
    async (_state, defaultKey) => {
      const created = serveSessionLifecycle();
      server.use(
        engineListingHandler({ engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)], defaultKey }),
      );
      const { result } = renderHook(() => useSessionWithEngines(SID));
      await waitFor(() => expect(result.current.chat.session.kind).toBe("ready"));
      await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
      await act(async () => { await result.current.chat.clear(); });
      expect(created).toEqual([{ engine_key: defaultKey }]);
    },
  );

  it("clear rejects with 'default repository not loaded' and makes no create request once the list has loaded but names no default", async () => {
    const created = serveSessionLifecycle();
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)] }));
    const { result } = renderHook(() => useSessionWithEngines(SID));
    await waitFor(() => expect(result.current.chat.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
    expect(result.current.defaultEngineKey).toBeNull();
    await act(async () => {
      await expect(result.current.chat.clear()).rejects.toThrow("default repository not loaded");
    });
    expect(created).toEqual([]);
  });

  it("clear keeps the session's own repository and project when the listed default names another", async () => {
    const created = serveSessionLifecycle({ engine_key: OWN_KEY, project_id: "proj-a" });
    server.use(engineListingHandler({ engines: [], defaultKey: LISTED_DEFAULT_KEY }));
    const { result } = renderHook(() => useSessionWithEngines(SID));
    await waitFor(() => expect(result.current.chat.engineKey).toBe(OWN_KEY));
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
    await act(async () => { await result.current.chat.clear(); });
    expect(created).toEqual([{ engine_key: OWN_KEY, project_id: "proj-a" }]);
  });

  it.each([
    { holder: "no repository of its own", overrides: {}, expectedKey: null },
    { holder: "its own repository", overrides: { engine_key: OWN_KEY }, expectedKey: OWN_KEY },
  ])(
    "leaves engineKey untouched when the list arrives naming another default for a session with $holder",
    async ({ overrides, expectedKey }) => {
      serveSessionLifecycle(overrides);
      const listing = gatedEngineListingHandler({ engines: [], defaultKey: LISTED_DEFAULT_KEY });
      server.use(listing.handler);
      const { result } = renderHook(() => useSessionWithEngines(SID));
      await waitFor(() => expect(result.current.chat.session.kind).toBe("ready"));
      await waitFor(() => expect(result.current.chat.engineKey).toBe(expectedKey));
      expect(result.current.defaultEngineKey).toBeNull();
      act(() => { listing.release(); });
      await waitFor(() => expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY));
      expect(result.current.chat.engineKey).toBe(expectedKey);
    },
  );

  it("retargets a later clear when the listed default changes without ever rewriting the binding", async () => {
    const created = serveSessionLifecycle();
    server.use(engineListingHandler({ engines: [], defaultKey: STUB_DEFAULT_ENGINE_KEY }));
    const { result } = renderHook(() => useSessionWithEngines(SID));
    await waitFor(() => expect(result.current.chat.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
    server.use(engineListingHandler({ engines: [], defaultKey: LISTED_DEFAULT_KEY }));
    act(() => { result.current.refreshEngines(); });
    await waitFor(() => expect(result.current.defaultEngineKey).toBe(LISTED_DEFAULT_KEY));
    expect(result.current.chat.engineKey).toBeNull();
    await act(async () => { await result.current.chat.clear(); });
    expect(created).toEqual([{ engine_key: LISTED_DEFAULT_KEY }]);
  });
});
