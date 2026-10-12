import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { api } from "./api";
import { server } from "./test/server";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const BASE = "/api/explore-chat/sessions";

const SESSION_RESP = {
  session_id: SID,
  consultant_key: "brainstorming",
  created_at: "2026-06-01T00:00:00Z",
  messages: [],
  in_flight_turn: null,
  brief: null,
};

describe("exploreChat API functions", () => {
  beforeEach(() => {
    server.use(
      http.post(BASE, () => HttpResponse.json({ session_id: SID }, { status: 201 })),
      http.get(`${BASE}/${SID}`, () => HttpResponse.json(SESSION_RESP)),
    );
  });

  it("createExploreChatSession POSTs consultant_key and engine_key, with no project_id when omitted", async () => {
    let captured: unknown;
    server.use(
      http.post(BASE, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ session_id: SID }, { status: 201 });
      }),
    );
    const result = await api.createExploreChatSession("brainstorming", "DejvMatikar/alan");
    expect(result.session_id).toBe(SID);
    expect(captured).toEqual({ consultant_key: "brainstorming", engine_key: "DejvMatikar/alan" });
  });

  it("createExploreChatSession forwards an explicit engine_key and project_id", async () => {
    let captured: unknown;
    server.use(
      http.post(BASE, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ session_id: SID }, { status: 201 });
      }),
    );
    const result = await api.createExploreChatSession("brainstorming", "acme/widget", "proj-a");
    expect(result.session_id).toBe(SID);
    expect(captured).toEqual({
      consultant_key: "brainstorming",
      engine_key: "acme/widget",
      project_id: "proj-a",
    });
  });

  it("getExploreChatSession GETs the session", async () => {
    const session = await api.getExploreChatSession(SID);
    expect(session.session_id).toBe(SID);
    expect(session.consultant_key).toBe("brainstorming");
  });

  it("deleteExploreChatSession sends DELETE", async () => {
    server.use(
      http.delete(`${BASE}/${SID}`, () => new HttpResponse(null, { status: 204 })),
    );
    await expect(api.deleteExploreChatSession(SID)).resolves.toBeUndefined();
  });

  it("deleteExploreChatSession throws on non-ok response", async () => {
    server.use(
      http.delete(`${BASE}/${SID}`, () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
    );
    await expect(api.deleteExploreChatSession(SID)).rejects.toThrow("404");
  });

  it("sendExploreChatMessage POSTs content", async () => {
    server.use(
      http.post(`${BASE}/${SID}/messages`, () => HttpResponse.json({ status: "accepted" }, { status: 202 })),
    );
    const r = await api.sendExploreChatMessage(SID, "hello");
    expect(r.status).toBe("accepted");
  });

  it("interruptExploreChatSession sends POST to /interrupt (ok)", async () => {
    server.use(
      http.post(`${BASE}/${SID}/interrupt`, () => new HttpResponse(null, { status: 204 })),
    );
    await expect(api.interruptExploreChatSession(SID)).resolves.toBeUndefined();
  });

  it("interruptExploreChatSession throws on a non-ok response", async () => {
    server.use(
      http.post(`${BASE}/${SID}/interrupt`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    await expect(api.interruptExploreChatSession(SID)).rejects.toThrow("500");
  });

  it("summarizeExploreChat POSTs to /summarize", async () => {
    server.use(
      http.post(`${BASE}/${SID}/summarize`, () =>
        HttpResponse.json({ content: "the brief", updated_at: "2026-06-01T01:00:00Z" }),
      ),
    );
    const brief = await api.summarizeExploreChat(SID);
    expect(brief.content).toBe("the brief");
  });

  it("putExploreChatBrief PUTs content", async () => {
    server.use(
      http.put(`${BASE}/${SID}/brief`, () =>
        HttpResponse.json({ content: "edited", updated_at: "2026-06-01T02:00:00Z" }),
      ),
    );
    const brief = await api.putExploreChatBrief(SID, "edited");
    expect(brief.content).toBe("edited");
  });

  it("getExploreChatBrief GETs brief", async () => {
    server.use(
      http.get(`${BASE}/${SID}/brief`, () =>
        HttpResponse.json({ content: "saved", updated_at: "2026-06-01T03:00:00Z" }),
      ),
    );
    const brief = await api.getExploreChatBrief(SID);
    expect(brief?.content).toBe("saved");
  });

  it("fetchConsultants returns list", async () => {
    server.use(
      http.get("/api/explore-chat/consultants", () =>
        HttpResponse.json([{ key: "brainstorming", label: "Brainstorming", description: "...", team: "brainstorming" }]),
      ),
    );
    const consultants = await api.fetchConsultants();
    expect(consultants).toHaveLength(1);
    expect(consultants.at(0)?.key).toBe("brainstorming");
  });

  it("subscribeExploreChatSession returns unsubscribe function", () => {
    const unsub = api.subscribeExploreChatSession(SID, {
      onEvent: () => undefined,
      onError: () => undefined,
    });
    expect(typeof unsub).toBe("function");
    unsub();
  });

});
