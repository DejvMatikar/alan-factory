import { HttpResponse, http, type JsonBodyType } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, api } from "./api";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { server } from "./test/server";

const TOKEN = "transport-token";
const TASK = "ws-1";
const SID = "12345678-1234-1234-1234-1234567890ab";
const PREP_CHAT_PATH = `/api/workspaces/${TASK}/prep-chat`;
const INVOCATIONS_PATH = `/api/workspaces/${TASK}/invocations`;
const MATCH = { label: "PR #12", kind: "pr", deep_link: "https://example.test/pull/12" };
const SECRET = {
  name: "acme/widget:transport",
  fingerprint: "abc123",
  last_verified: null,
  expires_at: null,
  expiry_warned_at: null,
};

interface SeenRequest {
  method: string;
  pathname: string;
  search: string;
  token: string | null;
  contentType: string | null;
  body: string;
}

function serve(verb: "get" | "post", path: string, respond: () => Response): SeenRequest[] {
  const seen: SeenRequest[] = [];
  server.use(
    http[verb](path, async ({ request }) => {
      const url = new URL(request.url);
      seen.push({
        method: request.method,
        pathname: url.pathname,
        search: url.search,
        token: request.headers.get(CONTROL_TOKEN_HEADER),
        contentType: request.headers.get("content-type"),
        body: await request.text(),
      });
      return respond();
    }),
  );
  return seen;
}

function json(body: JsonBodyType, status = 200): () => Response {
  return () => HttpResponse.json(body, { status });
}

async function rejection(call: () => Promise<unknown>): Promise<ApiError> {
  try {
    await call();
  } catch (err) {
    if (err instanceof ApiError) return err;
  }
  throw new Error("expected an ApiError rejection");
}

beforeEach(() => {
  clearControlToken();
  writeControlToken(TOKEN);
});

interface MigratedCall {
  label: string;
  verb: "get" | "post";
  path: string;
  call: () => Promise<unknown>;
}

const MIGRATED_CALLS: MigratedCall[] = [
  { label: "getWorkspacePrepChat", verb: "get", path: PREP_CHAT_PATH, call: () => api.getWorkspacePrepChat(TASK) },
  { label: "listInvocations", verb: "get", path: INVOCATIONS_PATH, call: () => api.listInvocations(TASK) },
  { label: "listSecrets", verb: "get", path: "/api/secrets", call: () => api.listSecrets() },
  {
    label: "createAndDispatchWorkspace",
    verb: "post",
    path: "/api/workspaces",
    call: () => api.createAndDispatchWorkspace("T", "D", "research"),
  },
  {
    label: "checkCollision",
    verb: "post",
    path: "/api/collision-check",
    call: () => api.checkCollision("a brief"),
  },
  {
    label: "createSecret",
    verb: "post",
    path: "/api/secrets",
    call: () => api.createSecret({ engine: "acme/widget", purpose: "transport", value: "v" }),
  },
  { label: "proposeBuildDispatch", verb: "post", path: `/api/build-chat/sessions/${SID}/propose`, call: () => api.proposeBuildDispatch(SID) },
  { label: "proposeExploreDispatch", verb: "post", path: `/api/explore-chat/sessions/${SID}/propose`, call: () => api.proposeExploreDispatch(SID) },
];

const ERROR_SHAPES = [
  { label: "a string detail", respond: () => HttpResponse.json({ detail: "gone" }, { status: 404 }), detail: "gone" },
  {
    label: "a structured detail",
    respond: () => HttpResponse.json({ detail: { detail: "busy", reason: "job finishing" } }, { status: 404 }),
    detail: "busy: job finishing",
  },
  {
    label: "an empty body",
    respond: () => HttpResponse.text("", { status: 404, statusText: "Not Found" }),
    detail: "Not Found",
  },
  {
    label: "a non-JSON body",
    respond: () => HttpResponse.text("upstream exploded", { status: 404, statusText: "Not Found" }),
    detail: "upstream exploded",
  },
] as const;

describe.each(MIGRATED_CALLS)("$label error mapping", ({ verb, path, call }) => {
  it.each(ERROR_SHAPES)("surfaces $label as an ApiError with the same status and detail", async (shape) => {
    server.use(http[verb](path, shape.respond));
    const err = await rejection(call);
    expect(err.status).toBe(404);
    expect(err.detail).toBe(shape.detail);
    expect(err.message).toBe(`404: ${shape.detail}`);
  });
});

describe("listSecrets", () => {
  it("returns the secrets array from a GET that carries no control token and no content type", async () => {
    const seen = serve("get", "/api/secrets", json({ secrets: [SECRET] }));
    expect(await api.listSecrets()).toEqual([SECRET]);
    expect(seen).toEqual([
      { method: "GET", pathname: "/api/secrets", search: "", token: null, contentType: null, body: "" },
    ]);
  });
});

describe("the migrated mutations carry the control token and a JSON body", () => {
  it("createAndDispatchWorkspace POSTs the composed body to /api/workspaces", async () => {
    const seen = serve("post", "/api/workspaces", json({ task_id: "ws-9", root_task_ids: ["ws-9"] }, 201));
    const res = await api.createAndDispatchWorkspace("T", "D", "research", { instructions: "  go  " });
    expect(res).toEqual({ task_id: "ws-9", root_task_ids: ["ws-9"] });
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "POST", pathname: "/api/workspaces", token: TOKEN, contentType: "application/json" });
    expect(JSON.parse(seen[0]!.body)).toEqual({ team: "research", title: "T", description: "D", instructions: "go" });
  });

  it("createSecret POSTs the payload including expires_at and returns the record", async () => {
    const seen = serve("post", "/api/secrets", json(SECRET, 201));
    const payload = { engine: "acme/widget", purpose: "transport", value: "ghp_abc", expires_at: "2027-01-01" };
    expect(await api.createSecret(payload)).toEqual(SECRET);
    expect(seen[0]).toMatchObject({ method: "POST", token: TOKEN, contentType: "application/json" });
    expect(JSON.parse(seen[0]!.body)).toEqual(payload);
  });

  it("createSecret omits the header when no token is stored", async () => {
    clearControlToken();
    const seen = serve("post", "/api/secrets", json(SECRET, 201));
    await api.createSecret({ engine: "acme/widget", purpose: "transport", value: "v" });
    expect(seen[0]?.token).toBeNull();
  });
});

describe("checkCollision", () => {
  it.each([
    ["no options", {}, { brief: "a brief" }],
    ["only the chat family", { chatFamily: "explore" }, { brief: "a brief", chat_family: "explore" }],
    ["only the session id", { sessionId: "sid-1" }, { brief: "a brief", session_id: "sid-1" }],
    [
      "both options",
      { chatFamily: "build", sessionId: "sid-1" },
      { brief: "a brief", chat_family: "build", session_id: "sid-1" },
    ],
  ] as const)("sends %s with every unset key omitted", async (_label, opts, body) => {
    const seen = serve("post", "/api/collision-check", json({ state: "green", sentence: "s", match: null }));
    await api.checkCollision("a brief", opts);
    expect(seen[0]).toMatchObject({ method: "POST", token: TOKEN, contentType: "application/json" });
    expect(JSON.parse(seen[0]!.body)).toEqual(body);
  });

  it("returns the verdict with its match", async () => {
    serve("post", "/api/collision-check", json({ state: "red", sentence: "Overlaps.", match: MATCH }));
    expect(await api.checkCollision("a brief")).toEqual({ state: "red", sentence: "Overlaps.", match: MATCH });
  });

  it("returns a null match for a verdict that names none", async () => {
    serve("post", "/api/collision-check", json({ state: "green", sentence: "Clear.", match: null }));
    expect(await api.checkCollision("a brief")).toEqual({ state: "green", sentence: "Clear.", match: null });
  });
});

describe("createSecret error text", () => {
  it("keeps a string detail verbatim", async () => {
    server.use(http.post("/api/secrets", () => HttpResponse.json({ detail: "value must not be empty" }, { status: 422 })));
    const err = await rejection(() => api.createSecret({ engine: "e", purpose: "p", value: "" }));
    expect(err.message).toBe("422: value must not be empty");
  });

  it("surfaces a list-shaped validation detail as the status text, no longer the raw JSON body", async () => {
    server.use(
      http.post("/api/secrets", () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "value"], msg: "Field required", type: "missing" }] },
          { status: 422, statusText: "Unprocessable Entity" },
        ),
      ),
    );
    const err = await rejection(() => api.createSecret({ engine: "e", purpose: "p", value: "" }));
    expect(err.status).toBe(422);
    expect(err.detail).toBe("Unprocessable Entity");
  });
});

describe("deleteSecret, a surviving raw-fetch caller, keeps the shared raw error text", () => {
  const DELETE_PAYLOAD = { engine: "acme/widget", purpose: "transport" };

  it.each(ERROR_SHAPES)("surfaces $label as an ApiError with the same status and detail", async (shape) => {
    server.use(http.delete("/api/secrets", shape.respond));
    const err = await rejection(() => api.deleteSecret(DELETE_PAYLOAD));
    expect(err.status).toBe(404);
    expect(err.detail).toBe(shape.detail);
  });

  it("surfaces a detail with no string fields as the raw JSON body", async () => {
    server.use(http.delete("/api/secrets", () => HttpResponse.json({ detail: { nested: 1 } }, { status: 422 })));
    const err = await rejection(() => api.deleteSecret(DELETE_PAYLOAD));
    expect(err.detail).toBe('{"detail":{"nested":1}}');
  });
});

describe("segment guards throw before any request is built", () => {
  it.each([
    ["getWorkspacePrepChat", () => api.getWorkspacePrepChat("BAD ID"), "invalid task_id: BAD ID"],
    ["getWorkspacePrepChat with an empty id", () => api.getWorkspacePrepChat(""), "invalid task_id: "],
    ["listInvocations", () => api.listInvocations("a b"), "invalid task_id: a b"],
    ["listInvocations with an empty id", () => api.listInvocations(""), "invalid task_id: "],
    ["proposeBuildDispatch", () => api.proposeBuildDispatch("not-a-uuid"), "invalid session_id: not-a-uuid"],
    ["proposeExploreDispatch", () => api.proposeExploreDispatch("not-a-uuid"), "invalid session_id: not-a-uuid"],
  ] as const)("%s", async (_label, call, message) => {
    let requests = 0;
    server.use(
      http.all("/api/*", () => {
        requests += 1;
        return HttpResponse.json({});
      }),
    );
    await expect(call()).rejects.toThrow(new Error(message));
    expect(requests).toBe(0);
  });
});
