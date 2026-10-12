import { HttpResponse, http, type JsonBodyType } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, api } from "./api";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { server } from "./test/server";

const TOKEN = "workspace-token";
const TASK = "ws-1";
const TASK_PATH = `/api/workspaces/${TASK}`;
const INV = "inv_001";
const INV_PATH = `${TASK_PATH}/invocations/${INV}`;

interface SeenRequest {
  method: string;
  pathname: string;
  search: string;
  token: string | null;
  body: string;
}

function serve(
  verb: "get" | "post" | "put" | "delete" | "all",
  path: string,
  respond: () => Response,
): SeenRequest[] {
  const seen: SeenRequest[] = [];
  server.use(
    http[verb](path, async ({ request }) => {
      const url = new URL(request.url);
      seen.push({
        method: request.method,
        pathname: url.pathname,
        search: url.search,
        token: request.headers.get(CONTROL_TOKEN_HEADER),
        body: await request.text(),
      });
      return respond();
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

describe("workspace mutations send the control token through the shared client", () => {
  it("deleteWorkspace sends it on DELETE /api/workspaces/{id}", async () => {
    const seen = serve("delete", TASK_PATH, noContent);
    await api.deleteWorkspace(TASK);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "DELETE", pathname: TASK_PATH, token: TOKEN, body: "" });
  });

  it("archiveWorkspace sends it on POST /archive", async () => {
    const seen = serve("post", `${TASK_PATH}/archive`, noContent);
    await api.archiveWorkspace(TASK);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "POST", pathname: `${TASK_PATH}/archive`, token: TOKEN, body: "" });
  });

  it("unarchiveWorkspace sends it on POST /unarchive", async () => {
    const seen = serve("post", `${TASK_PATH}/unarchive`, noContent);
    await api.unarchiveWorkspace(TASK);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "POST", pathname: `${TASK_PATH}/unarchive`, token: TOKEN, body: "" });
  });

  it("stopWorkspace sends it on POST /stop", async () => {
    const seen = serve("post", `${TASK_PATH}/stop`, json({ job_id: 7 }, 202));
    await api.stopWorkspace(TASK);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "POST", pathname: `${TASK_PATH}/stop`, token: TOKEN, body: "" });
  });

  it("resumeWorkspace sends it on POST /resume", async () => {
    const seen = serve("post", `${TASK_PATH}/resume`, json({ queued_jobs: 0 }, 202));
    await api.resumeWorkspace(TASK);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "POST", pathname: `${TASK_PATH}/resume`, token: TOKEN, body: "" });
  });

  it("clearStickyInstructions sends it on DELETE /sticky-instructions", async () => {
    const seen = serve("delete", `${TASK_PATH}/sticky-instructions`, noContent);
    await api.clearStickyInstructions(TASK);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({
      method: "DELETE",
      pathname: `${TASK_PATH}/sticky-instructions`,
      token: TOKEN,
      body: "",
    });
  });

  it("deleteWorkspace omits the header when no token is stored", async () => {
    clearControlToken();
    const seen = serve("delete", TASK_PATH, noContent);
    await api.deleteWorkspace(TASK);
    expect(seen[0]?.token).toBeNull();
  });
});

describe("workspace reads never send the control token", () => {
  it("listWorkspaces omits it, the middleware skipping GET", async () => {
    const seen = serve("get", "/api/workspaces", json([]));
    await api.listWorkspaces();
    expect(seen[0]).toMatchObject({ method: "GET", pathname: "/api/workspaces", search: "", token: null });
  });

  it("getWorkspace omits it, the middleware skipping GET", async () => {
    const seen = serve("get", TASK_PATH, json({ task_id: TASK }));
    await api.getWorkspace(TASK);
    expect(seen[0]).toMatchObject({ method: "GET", pathname: TASK_PATH, search: "", token: null });
  });
});

describe("workspace writes with a body", () => {
  it("updateWorkspaceNote PUTs the note as JSON and returns the stored note", async () => {
    const seen = serve("put", `${TASK_PATH}/note`, json({ note: "remember" }));
    const res = await api.updateWorkspaceNote(TASK, "remember");
    expect(res).toEqual({ note: "remember" });
    expect(seen[0]).toMatchObject({ method: "PUT", token: TOKEN });
    expect(JSON.parse(seen[0]!.body)).toEqual({ note: "remember" });
  });

  it("dispatchTask POSTs to the nested task path with the trimmed instructions", async () => {
    const path = `${TASK_PATH}/tasks/t2/dispatch`;
    const seen = serve("post", path, json({ job_id: 3, invocation_id: "inv_002" }, 202));
    const res = await api.dispatchTask(TASK, "t2", "implementation", { instructions: "  go  " });
    expect(res).toEqual({ job_id: 3, invocation_id: "inv_002" });
    expect(seen[0]).toMatchObject({ method: "POST", pathname: path, token: TOKEN });
    expect(JSON.parse(seen[0]!.body)).toEqual({ team: "implementation", instructions: "go" });
  });

  it("createWorkspace POSTs the title and description and returns the created ids", async () => {
    const seen = serve("post", "/api/workspaces", json({ task_id: "t", root_task_ids: ["t"] }, 201));
    const res = await api.createWorkspace("T", "D");
    expect(res).toEqual({ task_id: "t", root_task_ids: ["t"] });
    expect(seen[0]).toMatchObject({ method: "POST", token: TOKEN });
    expect(JSON.parse(seen[0]!.body)).toEqual({ title: "T", description: "D" });
  });
});

interface ReadCase {
  label: string;
  path: string;
  body: JsonBodyType;
  call: () => Promise<unknown>;
}

const READS: ReadCase[] = [
  { label: "getHandovers", path: `${TASK_PATH}/handovers`, body: [], call: () => api.getHandovers(TASK) },
  {
    label: "getDelivery",
    path: `${TASK_PATH}/delivery`,
    body: { pr_number: 4, repo: "o/r" },
    call: () => api.getDelivery(TASK),
  },
  {
    label: "getDispatchInstructions",
    path: `${TASK_PATH}/dispatch-instructions`,
    body: { [INV]: { status: "none", text: null, origin: null } },
    call: () => api.getDispatchInstructions(TASK),
  },
  {
    label: "getStickyInstructions",
    path: `${TASK_PATH}/sticky-instructions`,
    body: { text: "keep", origin: "dispatch" },
    call: () => api.getStickyInstructions(TASK),
  },
  { label: "getInvocation", path: INV_PATH, body: { id: INV }, call: () => api.getInvocation(TASK, INV) },
  {
    label: "listInvocations",
    path: `${TASK_PATH}/invocations`,
    body: [INV],
    call: () => api.listInvocations(TASK),
  },
  {
    label: "getWorkspacePrepChat",
    path: `${TASK_PATH}/prep-chat`,
    body: { session_id: "s1", kind: "build", consultant_key: null, title: "t", messages: [] },
    call: () => api.getWorkspacePrepChat(TASK),
  },
  {
    label: "getAgentStream",
    path: `${INV_PATH}/agents/researcher/stream`,
    body: [{ type: "system" }],
    call: () => api.getAgentStream(TASK, INV, "researcher"),
  },
  {
    label: "fetchNodeResult",
    path: `${INV_PATH}/agents/node_1/result`,
    body: { role: "r", model: "m", result_text: "done", completed_at: "t" },
    call: () => api.fetchNodeResult(TASK, INV, "node_1"),
  },
  {
    label: "fetchNodeConfig",
    path: `${INV_PATH}/agents/node_1/config`,
    body: { system_prompt: "s" },
    call: () => api.fetchNodeConfig(TASK, INV, "node_1"),
  },
];

describe("workspace reads", () => {
  it.each(READS)("$label issues a GET to $path and returns the parsed body", async (read) => {
    const seen = serve("get", read.path, json(read.body));
    const resp = await read.call();
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "GET", pathname: read.path, search: "", token: null });
    expect(resp).toEqual(read.body);
  });
});

describe("workspace path segment guards", () => {
  it.each([
    ["deleteWorkspace", () => api.deleteWorkspace("BAD ID"), "invalid task_id: BAD ID"],
    ["clearStickyInstructions", () => api.clearStickyInstructions("a/b"), "invalid task_id: a/b"],
    ["listInvocations", () => api.listInvocations("a b"), "invalid task_id: a b"],
    ["getWorkspacePrepChat", () => api.getWorkspacePrepChat("BAD ID"), "invalid task_id: BAD ID"],
    ["getInvocation", () => api.getInvocation(TASK, "inv_1"), "invalid inv_id: inv_1"],
    ["getAgentStream", () => api.getAgentStream(TASK, INV, "Bad Role"), "invalid role: Bad Role"],
    ["fetchNodeResult", () => api.fetchNodeResult(TASK, INV, "Bad Node"), "invalid node_id: Bad Node"],
    ["fetchNodeConfig", () => api.fetchNodeConfig(TASK, INV, "1st"), "invalid node_id: 1st"],
    ["dispatchTask", () => api.dispatchTask(TASK, "BAD", "implementation"), "invalid task_id: BAD"],
  ] as const)("%s rejects an unsafe segment before any request is built", async (_label, call, message) => {
    const seen = serve("all", "/api/*", json({}));
    await expect(call()).rejects.toThrow(message);
    expect(seen).toHaveLength(0);
  });
});

describe("workspace error mapping", () => {
  it("stopWorkspace carries the status and string detail of a 409", async () => {
    server.use(
      http.post(`${TASK_PATH}/stop`, () =>
        HttpResponse.json({ detail: "workspace is paused; cannot stop" }, { status: 409 }),
      ),
    );
    const err = await rejection(() => api.stopWorkspace(TASK));
    expect(err.status).toBe(409);
    expect(err.message).toBe("409: workspace is paused; cannot stop");
  });

  it("archiveWorkspace falls back to the status text when the error body is empty", async () => {
    server.use(
      http.post(`${TASK_PATH}/archive`, () =>
        HttpResponse.text("", { status: 409, statusText: "Conflict" }),
      ),
    );
    const err = await rejection(() => api.archiveWorkspace(TASK));
    expect(err.detail).toBe("Conflict");
  });

  it("deleteWorkspace surfaces a structured detail as code and reason", async () => {
    server.use(
      http.delete(TASK_PATH, () =>
        HttpResponse.json({ detail: { detail: "busy", reason: "job finishing" } }, { status: 409 }),
      ),
    );
    const err = await rejection(() => api.deleteWorkspace(TASK));
    expect(err.detail).toBe("busy: job finishing");
  });

  it("listInvocations flattens a structured detail the same way", async () => {
    server.use(
      http.get(`${TASK_PATH}/invocations`, () =>
        HttpResponse.json({ detail: { detail: "busy", reason: "job finishing" } }, { status: 409 }),
      ),
    );
    const err = await rejection(() => api.listInvocations(TASK));
    expect(err.detail).toBe("busy: job finishing");
  });

  it("clearStickyInstructions surfaces a non-JSON error body as raw text", async () => {
    server.use(
      http.delete(`${TASK_PATH}/sticky-instructions`, () =>
        HttpResponse.text("upstream exploded", { status: 502, statusText: "Bad Gateway" }),
      ),
    );
    const err = await rejection(() => api.clearStickyInstructions(TASK));
    expect(err.detail).toBe("upstream exploded");
  });

  it("getWorkspace maps a 404 into an ApiError", async () => {
    server.use(
      http.get(TASK_PATH, () => HttpResponse.json({ detail: "workspace not found" }, { status: 404 })),
    );
    const err = await rejection(() => api.getWorkspace(TASK));
    expect(err.status).toBe(404);
    expect(err.detail).toBe("workspace not found");
  });
});
