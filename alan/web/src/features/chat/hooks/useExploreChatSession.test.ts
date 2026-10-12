import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useInvalidateEnginesList } from "../../../api/queries";
import { chatErrorOf, isTurnRunning } from "./chatSessionReducer";
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
import { useExploreChatSession } from "./useExploreChatSession";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const SID2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const BASE = "/api/explore-chat/sessions";
const OWN_KEY = "acme/sprocket";
const CONSULTANT = "brainstorming";

function useSessionWithEngines(sessionId: string | null) {
  const chat = useExploreChatSession(sessionId, CONSULTANT);
  const { loaded, defaultEngineKey } = useEngines();
  return { chat, enginesLoaded: loaded, defaultEngineKey, refreshEngines: useInvalidateEnginesList() };
}

interface CreateBody {
  consultant_key?: string;
  engine_key?: string;
}

function serveSessionLifecycle(overrides: Record<string, unknown> = {}) {
  const created: CreateBody[] = [];
  server.use(
    http.get(`${BASE}/${SID}`, () => HttpResponse.json(sessionResp(overrides))),
    http.delete(`${BASE}/${SID}`, () => new HttpResponse(null, { status: 204 })),
    http.post(BASE, async ({ request }) => {
      created.push((await request.json()) as CreateBody);
      return HttpResponse.json({ session_id: SID2 }, { status: 201 });
    }),
  );
  return created;
}

function sessionResp(overrides = {}) {
  return {
    session_id: SID,
    consultant_key: "brainstorming",
    project_id: null,
    engine_key: null,
    repo_provision: "not_needed",
    created_at: "2026-06-01T00:00:00Z",
    messages: [],
    in_flight_turn: null,
    brief: null,
    ...overrides,
  };
}

describe("useExploreChatSession", () => {
  beforeEach(() => {
    server.use(
      http.get(`${BASE}/${SID}`, () => HttpResponse.json(sessionResp())),
    );
  });

  it("loads idle when sessionId is null", () => {
    const { result } = renderHook(() => useExploreChatSession(null, "brainstorming"));
    expect(result.current.session.kind).toBe("loading");
  });

  it("loads messages and reaches ready status", async () => {
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.messages).toHaveLength(0);
  });

  it("ignores a session fetch (including the engine_key binding's own read) that resolves after unmount", async () => {
    const resolvers: ((value: Response) => void)[] = [];
    server.use(
      http.get(
        `${BASE}/${SID}`,
        () =>
          new Promise<Response>((res) => {
            resolvers.push(res);
          }),
      ),
    );
    const { unmount } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await new Promise((r) => setTimeout(r, 5));
    unmount();
    resolvers.forEach((res) => {
      res(HttpResponse.json(sessionResp()));
    });
    await new Promise((r) => setTimeout(r, 10));
  });

  it("loads brief when session has one", async () => {
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(
          sessionResp({ brief: { content: "existing brief", updated_at: "2026-06-01T01:00:00Z" } }),
        ),
      ),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.brief?.content).toBe("existing brief"));
  });

  it("exposes project_id and repo_provision from the session GET response", async () => {
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(sessionResp({ project_id: "proj-9", repo_provision: "ready" })),
      ),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    expect(result.current.projectId).toBe("proj-9");
    expect(result.current.repoProvision).toBe("ready");
  });

  it("starts a running turn when in_flight_turn exists", async () => {
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(
          sessionResp({ in_flight_turn: { started_at: "t", user_text: "q" } }),
        ),
      ),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(isTurnRunning(result.current.turn)).toBe(true));
  });

  it("sendMessage throws when sessionId is null", async () => {
    const { result } = renderHook(() => useExploreChatSession(null, "brainstorming"));
    await expect(result.current.sendMessage("hi")).rejects.toThrow("no explore-chat session");
  });

  it("sendMessage dispatches and reaches ready", async () => {
    server.use(
      http.post(`${BASE}/${SID}/messages`, () =>
        HttpResponse.json({ status: "accepted" }, { status: 202 }),
      ),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.sendMessage("hello");
    });
    expect(isTurnRunning(result.current.turn)).toBe(true);
  });

  it("summarize calls API and updates brief", async () => {
    server.use(
      http.post(`${BASE}/${SID}/summarize`, () =>
        HttpResponse.json({ content: "summarized", updated_at: "2026-06-01T02:00:00Z" }),
      ),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.summarize();
    });
    expect(result.current.brief?.content).toBe("summarized");
  });

  it("summarize throws when sessionId is null", async () => {
    const { result } = renderHook(() => useExploreChatSession(null, "brainstorming"));
    await expect(result.current.summarize()).rejects.toThrow("no explore-chat session");
  });

  it("updateBrief calls PUT and updates state", async () => {
    server.use(
      http.put(`${BASE}/${SID}/brief`, () =>
        HttpResponse.json({ content: "manual edit", updated_at: "2026-06-01T03:00:00Z" }),
      ),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.updateBrief("manual edit");
    });
    expect(result.current.brief?.content).toBe("manual edit");
  });

  it("updateBrief throws when sessionId is null", async () => {
    const { result } = renderHook(() => useExploreChatSession(null, "brainstorming"));
    await expect(result.current.updateBrief("x")).rejects.toThrow("no explore-chat session");
  });

  it("clear deletes old session and creates a new one", async () => {
    server.use(
      http.delete(`${BASE}/${SID}`, () => new HttpResponse(null, { status: 204 })),
      http.post(BASE, () => HttpResponse.json({ session_id: SID2 }, { status: 201 })),
    );
    const { result } = renderHook(() => useSessionWithEngines(SID));
    await waitFor(() => expect(result.current.chat.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
    let newId = "";
    await act(async () => {
      newId = await result.current.chat.clear();
    });
    expect(newId).toBe(SID2);
  });

  it("clear works even with null sessionId (skips delete)", async () => {
    server.use(
      http.post(BASE, () => HttpResponse.json({ session_id: SID2 }, { status: 201 })),
    );
    const { result } = renderHook(() => useSessionWithEngines(null));
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
    let newId = "";
    await act(async () => { newId = await result.current.chat.clear(); });
    expect(newId).toBe(SID2);
  });

  it("clear forwards the session's own engine_key into the replacement session", async () => {
    let getCalls = 0;
    let capturedBody: { consultant_key?: string; engine_key?: string } | null = null;
    server.use(
      http.get(`${BASE}/${SID}`, () => {
        getCalls += 1;
        return HttpResponse.json(sessionResp({ engine_key: "acme/sprocket" }));
      }),
      http.delete(`${BASE}/${SID}`, () => new HttpResponse(null, { status: 204 })),
      http.post(BASE, async ({ request }) => {
        capturedBody = (await request.json()) as { consultant_key?: string; engine_key?: string };
        return HttpResponse.json({ session_id: SID2 }, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.engineKey).toBe("acme/sprocket"));
    await act(async () => {
      await result.current.clear();
    });
    expect(capturedBody).toEqual({
      consultant_key: "brainstorming",
      engine_key: "acme/sprocket",
    });
    expect(getCalls).toBe(1);
  });

  it("ignores a binding fetch failure that resolves after unmount", async () => {
    const rejecters: ((reason: unknown) => void)[] = [];
    server.use(
      http.get(
        `${BASE}/${SID}`,
        () =>
          new Promise<Response>((_resolve, reject) => {
            rejecters.push(reject);
          }),
      ),
    );
    const { unmount } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await new Promise((r) => setTimeout(r, 5));
    unmount();
    rejecters.forEach((reject) => reject(new Error("late binding failure")));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("clear rejects instead of defaulting when the session's own binding fetch fails", async () => {
    let sid2GetCalls = 0;
    let createCalls = 0;
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(sessionResp({ engine_key: "acme/sprocket" })),
      ),
      http.get(`${BASE}/${SID2}`, () => {
        sid2GetCalls += 1;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
      http.delete(`${BASE}/${SID2}`, () => new HttpResponse(null, { status: 204 })),
      http.post(BASE, () => {
        createCalls += 1;
        return HttpResponse.json({ session_id: "should-not-be-used" }, { status: 201 });
      }),
    );
    const initialProps: { sid: string } = { sid: SID };
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string }) => useExploreChatSession(sid, "brainstorming"),
      { initialProps },
    );
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    rerender({ sid: SID2 });
    await waitFor(() => expect(result.current.session.kind).toBe("load_failed"));
    await waitFor(() => expect(sid2GetCalls).toBe(1));
    await act(async () => {
      await expect(result.current.clear()).rejects.toThrow(
        "repository unresolved for this session",
      );
    });
    expect(createCalls).toBe(0);
  });

  it("interrupt calls API", async () => {
    server.use(
      http.post(`${BASE}/${SID}/interrupt`, () => new HttpResponse(null, { status: 204 })),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await result.current.interrupt();
    });
  });

  it("interrupt does nothing when sessionId is null", async () => {
    const { result } = renderHook(() => useExploreChatSession(null, "brainstorming"));
    await act(async () => {
      await result.current.interrupt();
    });
  });

  it("cancelQueued clears queued message", async () => {
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    act(() => result.current.cancelQueued());
    expect(result.current.queuedMessage).toBeNull();
  });

  it("reports error when session load fails", async () => {
    server.use(
      http.get(`${BASE}/${SID}`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("load_failed"));
    expect(result.current.engineKey).toBeNull();
  });

  it("issues exactly one GET for the session on mount", async () => {
    let calls = 0;
    server.use(
      http.get(`${BASE}/${SID}`, () => {
        calls += 1;
        return HttpResponse.json(sessionResp({ engine_key: "acme/sprocket" }));
      }),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await waitFor(() => expect(result.current.engineKey).toBe("acme/sprocket"));
    expect(calls).toBe(1);
  });

  it("retains engineKey across a failed background refresh after a prior success", async () => {
    let calls = 0;
    server.use(
      http.get(`${BASE}/${SID}`, () => {
        calls += 1;
        if (calls === 1) return HttpResponse.json(sessionResp({ engine_key: "acme/sprocket" }));
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.engineKey).toBe("acme/sprocket"));
    await act(async () => {
      await result.current.refreshRunSummary();
    });
    expect(result.current.engineKey).toBe("acme/sprocket");
  });

  it("interrupt surfaces error and rethrows", async () => {
    server.use(
      http.post(`${BASE}/${SID}/interrupt`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(result.current.session.kind).toBe("ready"));
    await act(async () => {
      await expect(result.current.interrupt()).rejects.toThrow();
    });
    expect(chatErrorOf(result.current.turn)).toBeTruthy();
  });

  it("sendMessage queues while thinking and flushes after assistant_done", async () => {
    const sent: string[] = [];
    let sseCtl: ReadableStreamDefaultController<Uint8Array> | null = null;
    const enc = new TextEncoder();
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(
          sessionResp({ in_flight_turn: { started_at: "t", user_text: "first" } }),
        ),
      ),
      http.get(`${BASE}/${SID}/events`, () =>
        new HttpResponse(
          new ReadableStream<Uint8Array>({ start(ctl) { sseCtl = ctl; } }),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
      http.post(`${BASE}/${SID}/messages`, async ({ request }) => {
        const body = await request.json() as { content: string };
        sent.push(body.content);
        return HttpResponse.json({ status: "accepted" }, { status: 202 });
      }),
    );
    const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
    await waitFor(() => expect(isTurnRunning(result.current.turn)).toBe(true));
    await act(async () => { await result.current.sendMessage("second"); });
    expect(result.current.queuedMessage).toBe("second");
    act(() => {
      sseCtl?.enqueue(enc.encode(`event: assistant_done\ndata: ${JSON.stringify({ content: "reply", usage: "{}" })}\n\n`));
    });
    await waitFor(() => expect(sent).toContain("second"), { timeout: 3000 });
  });

  describe("SSE event handling", () => {
    const enc = new TextEncoder();

    function stubPushableSse(): { push: (chunk: string) => void } {
      const ctl: { c: ReadableStreamDefaultController<Uint8Array> | null } = { c: null };
      server.use(
        http.get(`${BASE}/${SID}`, () => HttpResponse.json(sessionResp())),
        http.get(`${BASE}/${SID}/events`, () =>
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
      const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
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
      const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => { sse.push("event: assistant_done\ndata: not-json\n\n"); });
      await waitFor(() => expect(result.current.messages).toHaveLength(1));
      expect(result.current.messages[0]?.content).toBe("");
    });

    it("handles assistant_done without content field (uses empty string fallback)", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => { sse.push(`event: assistant_done\ndata: ${JSON.stringify({ usage: "{}" })}\n\n`); });
      await waitFor(() => expect(result.current.messages).toHaveLength(1));
      expect(result.current.messages[0]?.content).toBe("");
    });

    it("ignores unknown SSE event types", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => { sse.push("event: unknown_event\ndata: {}\n\n"); });
      expect(result.current.messages).toHaveLength(0);
      expect(result.current.toolActivity).toHaveLength(0);
    });

    it("handles error event", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      act(() => {
        sse.push(`event: error\ndata: ${JSON.stringify({ detail: "failed" })}\n\n`);
      });
      await waitFor(() => expect(chatErrorOf(result.current.turn)).toBe("failed"));
    });

    it("handles chat_event (tool activity)", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
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
        http.get(`${BASE}/${SID}`, () => HttpResponse.json(sessionResp())),
        http.get(`${BASE}/${SID}/events`, () =>
          HttpResponse.json({ detail: "stream error" }, { status: 500 }),
        ),
      );
      const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
      await waitFor(() => expect(result.current.session.kind).toBe("ready"));
      await waitFor(() => expect(chatErrorOf(result.current.turn)).toBeTruthy());
    });

    it("handles turn_stopped: appends an interrupted assistant message and clears thinking", async () => {
      const sse = stubPushableSse();
      const { result } = renderHook(() => useExploreChatSession(SID, "brainstorming"));
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

describe("useExploreChatSession — default repository fallback", () => {
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
      http.get(`${BASE}/${SID}`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
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
      expect(created).toEqual([{ consultant_key: CONSULTANT, engine_key: defaultKey }]);
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

  it("clear keeps the session's own repository when the listed default names another", async () => {
    const created = serveSessionLifecycle({ engine_key: OWN_KEY });
    server.use(engineListingHandler({ engines: [], defaultKey: LISTED_DEFAULT_KEY }));
    const { result } = renderHook(() => useSessionWithEngines(SID));
    await waitFor(() => expect(result.current.chat.engineKey).toBe(OWN_KEY));
    await waitFor(() => expect(result.current.enginesLoaded).toBe(true));
    await act(async () => { await result.current.chat.clear(); });
    expect(created).toEqual([{ consultant_key: CONSULTANT, engine_key: OWN_KEY }]);
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
    expect(created).toEqual([{ consultant_key: CONSULTANT, engine_key: LISTED_DEFAULT_KEY }]);
  });
});
