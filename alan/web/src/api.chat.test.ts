import { HttpResponse, http, type JsonBodyType } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, api } from "./api";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { makeChatMessage, makeChatSession } from "./test/chatFixtures";
import { server } from "./test/server";

const TOKEN = "chat-token";
const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const BUILD = "/api/build-chat/sessions";
const EXPLORE = "/api/explore-chat/sessions";
const BRIEF = { content: "the brief", updated_at: "2026-06-01T01:00:00Z" };

type Verb = "get" | "post" | "put" | "delete";

interface SeenRequest {
  method: string;
  pathname: string;
  token: string | null;
  contentType: string | null;
  body: string;
}

function serve(verb: Verb, path: string, respond: () => Response): SeenRequest[] {
  const seen: SeenRequest[] = [];
  server.use(
    http[verb](path, async ({ request }) => {
      seen.push({
        method: request.method,
        pathname: new URL(request.url).pathname,
        token: request.headers.get(CONTROL_TOKEN_HEADER),
        contentType: request.headers.get("content-type"),
        body: await request.text(),
      });
      return respond();
    }),
  );
  return seen;
}

function serveAnything(): SeenRequest[] {
  const seen: SeenRequest[] = [];
  server.use(
    http.all("/api/*", ({ request }) => {
      seen.push({
        method: request.method,
        pathname: new URL(request.url).pathname,
        token: null,
        contentType: null,
        body: "",
      });
      return HttpResponse.json({});
    }),
  );
  return seen;
}

function noContent(): Response {
  return new HttpResponse(null, { status: 204 });
}

function json(body: JsonBodyType, status = 200): () => Response {
  return () => HttpResponse.json(body, { status });
}

beforeEach(() => {
  clearControlToken();
  writeControlToken(TOKEN);
});

interface MutationCase {
  label: string;
  verb: Verb;
  path: string;
  respond: () => Response;
  call: () => Promise<unknown>;
  body: string;
}

const MUTATIONS: MutationCase[] = [
  {
    label: "createBuildChatSession",
    verb: "post",
    path: BUILD,
    respond: json({ session_id: SID }, 201),
    call: () => api.createBuildChatSession("acme/widget", "proj-a"),
    body: JSON.stringify({ engine_key: "acme/widget", project_id: "proj-a" }),
  },
  {
    label: "deleteBuildChatSession",
    verb: "delete",
    path: `${BUILD}/${SID}`,
    respond: noContent,
    call: () => api.deleteBuildChatSession(SID),
    body: "",
  },
  {
    label: "sendBuildChatMessage",
    verb: "post",
    path: `${BUILD}/${SID}/messages`,
    respond: json({ status: "accepted" }, 202),
    call: () => api.sendBuildChatMessage(SID, "hello"),
    body: JSON.stringify({ content: "hello" }),
  },
  {
    label: "interruptBuildChatSession",
    verb: "post",
    path: `${BUILD}/${SID}/interrupt`,
    respond: noContent,
    call: () => api.interruptBuildChatSession(SID),
    body: "",
  },
  {
    label: "summarizeBuildChat",
    verb: "post",
    path: `${BUILD}/${SID}/summarize`,
    respond: json(BRIEF),
    call: () => api.summarizeBuildChat(SID),
    body: "",
  },
  {
    label: "putBuildChatBrief",
    verb: "put",
    path: `${BUILD}/${SID}/brief`,
    respond: json(BRIEF),
    call: () => api.putBuildChatBrief(SID, "edited"),
    body: JSON.stringify({ content: "edited" }),
  },
  {
    label: "createExploreChatSession",
    verb: "post",
    path: EXPLORE,
    respond: json({ session_id: SID }, 201),
    call: () => api.createExploreChatSession("brainstorming", "acme/widget"),
    body: JSON.stringify({ consultant_key: "brainstorming", engine_key: "acme/widget" }),
  },
  {
    label: "deleteExploreChatSession",
    verb: "delete",
    path: `${EXPLORE}/${SID}`,
    respond: noContent,
    call: () => api.deleteExploreChatSession(SID),
    body: "",
  },
  {
    label: "sendExploreChatMessage",
    verb: "post",
    path: `${EXPLORE}/${SID}/messages`,
    respond: json({ status: "accepted" }, 202),
    call: () => api.sendExploreChatMessage(SID, "hello"),
    body: JSON.stringify({ content: "hello" }),
  },
  {
    label: "interruptExploreChatSession",
    verb: "post",
    path: `${EXPLORE}/${SID}/interrupt`,
    respond: noContent,
    call: () => api.interruptExploreChatSession(SID),
    body: "",
  },
  {
    label: "summarizeExploreChat",
    verb: "post",
    path: `${EXPLORE}/${SID}/summarize`,
    respond: json(BRIEF),
    call: () => api.summarizeExploreChat(SID),
    body: "",
  },
  {
    label: "putExploreChatBrief",
    verb: "put",
    path: `${EXPLORE}/${SID}/brief`,
    respond: json(BRIEF),
    call: () => api.putExploreChatBrief(SID, "edited"),
    body: JSON.stringify({ content: "edited" }),
  },
  {
    label: "proposeBuildDispatch",
    verb: "post",
    path: `${BUILD}/${SID}/propose`,
    respond: json({ status: "need_more_info", questions: [] }),
    call: () => api.proposeBuildDispatch(SID),
    body: "",
  },
  {
    label: "proposeExploreDispatch",
    verb: "post",
    path: `${EXPLORE}/${SID}/propose`,
    respond: json({ status: "need_more_info", questions: [] }),
    call: () => api.proposeExploreDispatch(SID),
    body: "",
  },
];

describe("chat mutations send the control token through the shared client", () => {
  it.each(MUTATIONS)("$label sends it on $verb $path with the unchanged body", async (mutation) => {
    const seen = serve(mutation.verb, mutation.path, mutation.respond);
    await mutation.call();
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({
      method: mutation.verb.toUpperCase(),
      pathname: mutation.path,
      token: TOKEN,
      body: mutation.body,
    });
  });

  it.each(MUTATIONS)("$label omits the header when no token is stored", async (mutation) => {
    clearControlToken();
    const seen = serve(mutation.verb, mutation.path, mutation.respond);
    await mutation.call();
    expect(seen[0]?.token).toBeNull();
  });

  it.each(MUTATIONS.filter((mutation) => mutation.body === ""))(
    "$label declares no JSON content type on its bodiless request",
    async (mutation) => {
      const seen = serve(mutation.verb, mutation.path, mutation.respond);
      await mutation.call();
      expect(seen[0]?.contentType).toBeNull();
    },
  );
});

describe("chat reads carry no control token", () => {
  it("getBuildChatSession sends no token header", async () => {
    const seen = serve("get", `${BUILD}/${SID}`, json(makeChatSession()));
    await api.getBuildChatSession(SID);
    expect(seen[0]?.token).toBeNull();
  });

  it("fetchConsultants sends no token header", async () => {
    const seen = serve("get", "/api/explore-chat/consultants", json([]));
    await api.fetchConsultants();
    expect(seen[0]?.token).toBeNull();
  });
});

const INVALID = "not-a-uuid";

const SESSION_ID_CALLS: [string, () => Promise<unknown>][] = [
  ["getBuildChatSession", () => api.getBuildChatSession(INVALID)],
  ["deleteBuildChatSession", () => api.deleteBuildChatSession(INVALID)],
  ["sendBuildChatMessage", () => api.sendBuildChatMessage(INVALID, "x")],
  ["interruptBuildChatSession", () => api.interruptBuildChatSession(INVALID)],
  ["summarizeBuildChat", () => api.summarizeBuildChat(INVALID)],
  ["putBuildChatBrief", () => api.putBuildChatBrief(INVALID, "x")],
  ["getBuildChatBrief", () => api.getBuildChatBrief(INVALID)],
  ["proposeBuildDispatch", () => api.proposeBuildDispatch(INVALID)],
  ["proposeExploreDispatch", () => api.proposeExploreDispatch(INVALID)],
  ["getExploreChatSession", () => api.getExploreChatSession(INVALID)],
  ["deleteExploreChatSession", () => api.deleteExploreChatSession(INVALID)],
  ["sendExploreChatMessage", () => api.sendExploreChatMessage(INVALID, "x")],
  ["interruptExploreChatSession", () => api.interruptExploreChatSession(INVALID)],
  ["summarizeExploreChat", () => api.summarizeExploreChat(INVALID)],
  ["putExploreChatBrief", () => api.putExploreChatBrief(INVALID, "x")],
  ["getExploreChatBrief", () => api.getExploreChatBrief(INVALID)],
];

describe("chat requests validate the session id before any request is built", () => {
  it.each(SESSION_ID_CALLS)("%s rejects an invalid session_id and issues no request", async (_label, call) => {
    const seen = serveAnything();
    await expect(call()).rejects.toThrow(`invalid session_id: ${INVALID}`);
    expect(seen).toHaveLength(0);
  });
});

describe("chat session reads", () => {
  it("getBuildChatSession keeps the interrupted flag on a stopped turn across a reload", async () => {
    const stopped = makeChatMessage({ content: "partial", interrupted: true });
    serve("get", `${BUILD}/${SID}`, json(makeChatSession({ messages: [stopped] })));
    const session = await api.getBuildChatSession(SID);
    expect(session.messages.at(-1)).toEqual(stopped);
  });

  it("getExploreChatSession keeps the interrupted flag on a stopped turn across a reload", async () => {
    const stopped = makeChatMessage({ content: "partial", interrupted: true });
    serve("get", `${EXPLORE}/${SID}`, json({ ...makeChatSession({ messages: [stopped] }), consultant_key: "brainstorming" }));
    const session = await api.getExploreChatSession(SID);
    expect(session.messages.at(-1)).toEqual(stopped);
  });

  it("getBuildChatBrief resolves null when the session has no brief", async () => {
    serve("get", `${BUILD}/${SID}/brief`, json(null));
    await expect(api.getBuildChatBrief(SID)).resolves.toBeNull();
  });

  it("getBuildChatSession rejects a 404 with an ApiError carrying the detail", async () => {
    serve("get", `${BUILD}/${SID}`, json({ detail: "build-chat session not found" }, 404));
    await expect(api.getBuildChatSession(SID)).rejects.toMatchObject({
      status: 404,
      detail: "build-chat session not found",
    });
  });

  it("interruptBuildChatSession rejects a non-ok response with an ApiError", async () => {
    serve("post", `${BUILD}/${SID}/interrupt`, json({ detail: "build-chat session not found" }, 404));
    await expect(api.interruptBuildChatSession(SID)).rejects.toBeInstanceOf(ApiError);
  });
});
