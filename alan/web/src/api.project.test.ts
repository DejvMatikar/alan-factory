import { HttpResponse, http, type JsonBodyType } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, api } from "./api";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { makeProject, makeProjectDetail, makeProjectListEntry } from "./test/projectFixtures";
import { server } from "./test/server";

const TOKEN = "project-token";
const PROJECT = "alpha";
const PROJECTS = "/api/projects";
const PROJECT_PATH = `${PROJECTS}/${PROJECT}`;
const ROADMAP_PATH = `${PROJECT_PATH}/roadmap`;
const PROPOSAL = { fingerprint: "fp1", project_id: PROJECT, proposed_at: "2026-01-01T00:00:00Z" };

type Verb = "get" | "post" | "patch" | "delete" | "all";

interface SeenRequest {
  method: string;
  pathname: string;
  search: string;
  token: string | null;
  body: string;
}

function serve(verb: Verb, path: string, respond: () => Response): SeenRequest[] {
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
    label: "createProject",
    verb: "post",
    path: PROJECTS,
    respond: json(makeProject({ id: PROJECT }), 201),
    call: () => api.createProject("T", "docs/x.md"),
    body: JSON.stringify({ title: "T", doc_ref: "docs/x.md" }),
  },
  {
    label: "updateProject",
    verb: "patch",
    path: PROJECT_PATH,
    respond: json(makeProject({ id: PROJECT })),
    call: () => api.updateProject(PROJECT, { title: "x" }),
    body: JSON.stringify({ title: "x" }),
  },
  {
    label: "completeProject",
    verb: "post",
    path: `${PROJECT_PATH}/complete`,
    respond: json(makeProject({ id: PROJECT, status: "completed" })),
    call: () => api.completeProject(PROJECT, { force: true, reason: "shipped" }),
    body: JSON.stringify({ force: true, reason: "shipped" }),
  },
  {
    label: "deleteProject",
    verb: "delete",
    path: PROJECT_PATH,
    respond: noContent,
    call: () => api.deleteProject(PROJECT),
    body: "",
  },
  {
    label: "proposeProjectAmendment",
    verb: "post",
    path: `${PROJECT_PATH}/amendments`,
    respond: json(PROPOSAL, 201),
    call: () => api.proposeProjectAmendment(PROJECT, "v", "s", "d"),
    body: JSON.stringify({ vision: "v", scope: "s", decision_entry: "d" }),
  },
  {
    label: "previewRoadmap",
    verb: "post",
    path: `${ROADMAP_PATH}/preview`,
    respond: json({ items: [{ title: "A", text: "a" }] }),
    call: () => api.previewRoadmap(PROJECT, "# md"),
    body: JSON.stringify({ markdown: "# md" }),
  },
  {
    label: "importRoadmap",
    verb: "post",
    path: `${ROADMAP_PATH}/import`,
    respond: json({ items: [{ id: "itm_1", title: "A", text: "a" }] }),
    call: () => api.importRoadmap(PROJECT, "# md"),
    body: JSON.stringify({ markdown: "# md" }),
  },
  {
    label: "proposeRoadmapReorder",
    verb: "post",
    path: `${ROADMAP_PATH}/reorder`,
    respond: json(PROPOSAL, 201),
    call: () => api.proposeRoadmapReorder(PROJECT, ["b", "a"]),
    body: JSON.stringify({ ordered_ids: ["b", "a"] }),
  },
  {
    label: "proposeRoadmapEdit",
    verb: "post",
    path: `${ROADMAP_PATH}/items/itm_1`,
    respond: json(PROPOSAL, 201),
    call: () => api.proposeRoadmapEdit(PROJECT, "itm_1", { title: "t", text: "x" }),
    body: JSON.stringify({ title: "t", text: "x" }),
  },
  {
    label: "advanceProjectRoadmap",
    verb: "post",
    path: `${ROADMAP_PATH}/advance`,
    respond: json({ task_id: "ws-1", item_id: "itm_1", item_title: "A" }, 201),
    call: () => api.advanceProjectRoadmap(PROJECT),
    body: "",
  },
];

describe("project mutations send the control token through the shared client", () => {
  it.each(MUTATIONS)("$label sends it on $verb $path with the unchanged body", async (mutation) => {
    const seen = serve(mutation.verb, mutation.path, mutation.respond);
    await mutation.call();
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({
      method: mutation.verb.toUpperCase(),
      pathname: mutation.path,
      search: "",
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
});

describe("project reads never send the control token", () => {
  it("listProjects omits it and sends no query by default", async () => {
    const rows = [makeProjectListEntry({ id: PROJECT })];
    const seen = serve("get", PROJECTS, json(rows));
    expect(await api.listProjects()).toEqual(rows);
    expect(seen[0]).toMatchObject({ method: "GET", pathname: PROJECTS, search: "", token: null });
  });

  it("listProjects asks for archived projects with include=archived", async () => {
    const seen = serve("get", PROJECTS, json([]));
    await api.listProjects({ includeArchived: true });
    expect(seen[0]).toMatchObject({ method: "GET", search: "?include=archived", token: null });
  });

  it("listProjects sends no query when archived projects are not requested", async () => {
    const seen = serve("get", PROJECTS, json([]));
    await api.listProjects({ includeArchived: false });
    expect(seen[0]?.search).toBe("");
  });

  it("getProject omits it and returns the detail unchanged", async () => {
    const detail = makeProjectDetail({ id: PROJECT });
    const seen = serve("get", PROJECT_PATH, json(detail));
    expect(await api.getProject(PROJECT)).toEqual(detail);
    expect(seen[0]).toMatchObject({ method: "GET", pathname: PROJECT_PATH, token: null });
  });

  it("getProjectDoc omits it", async () => {
    const doc = { vision: "v", scope: "s", decision_log: "d", discovered_facts: "f" };
    const seen = serve("get", `${PROJECT_PATH}/doc`, json(doc));
    expect(await api.getProjectDoc(PROJECT)).toEqual(doc);
    expect(seen[0]).toMatchObject({ method: "GET", token: null });
  });

  it("getRoadmapNextItemProposal omits it", async () => {
    const reply = { proposal: null, budget_state: "normal" };
    const seen = serve("get", `${ROADMAP_PATH}/next-item-proposal`, json(reply));
    expect(await api.getRoadmapNextItemProposal(PROJECT)).toEqual(reply);
    expect(seen[0]).toMatchObject({ method: "GET", token: null });
  });
});

describe("project writes with optional fields", () => {
  it("createProject adds the intended engine key only when one is given", async () => {
    const seen = serve("post", PROJECTS, json(makeProject(), 201));
    await api.createProject("T", null, "acme/widget");
    await api.createProject("T", null, null);
    await api.createProject("T", null);
    expect(seen.map((s) => s.body)).toEqual([
      JSON.stringify({ title: "T", doc_ref: null, intended_engine_key: "acme/widget" }),
      JSON.stringify({ title: "T", doc_ref: null }),
      JSON.stringify({ title: "T", doc_ref: null }),
    ]);
  });

  it("completeProject defaults to force=false and an empty reason", async () => {
    const seen = serve("post", `${PROJECT_PATH}/complete`, json(makeProject()));
    await api.completeProject(PROJECT);
    expect(seen[0]?.body).toBe(JSON.stringify({ force: false, reason: "" }));
  });

  it("updateProject sends exactly the keys it was given", async () => {
    const seen = serve("patch", PROJECT_PATH, json(makeProject()));
    await api.updateProject(PROJECT, { title: "x" });
    await api.updateProject(PROJECT, {});
    await api.updateProject(PROJECT, { doc_ref: null, budget_usd: null });
    expect(seen.map((s) => s.body)).toEqual([
      JSON.stringify({ title: "x" }),
      JSON.stringify({}),
      JSON.stringify({ doc_ref: null, budget_usd: null }),
    ]);
  });

  it("updateProject drops a key set to undefined instead of sending it as null", async () => {
    const seen = serve("patch", PROJECT_PATH, json(makeProject()));
    const payload = { title: undefined, autonomy: "propose" } as unknown as Parameters<
      typeof api.updateProject
    >[1];
    await api.updateProject(PROJECT, payload);
    expect(seen[0]?.body).toBe(JSON.stringify({ autonomy: "propose" }));
  });
});

describe("project path segment guards", () => {
  it.each([
    ["getProject", () => api.getProject("BAD ID"), "invalid project_id: BAD ID"],
    ["updateProject", () => api.updateProject("a/b", {}), "invalid project_id: a/b"],
    ["deleteProject", () => api.deleteProject("-x"), "invalid project_id: -x"],
    ["completeProject", () => api.completeProject("BAD"), "invalid project_id: BAD"],
    ["getProjectDoc", () => api.getProjectDoc("BAD"), "invalid project_id: BAD"],
    [
      "proposeProjectAmendment",
      () => api.proposeProjectAmendment("BAD", "v", "s", "d"),
      "invalid project_id: BAD",
    ],
    ["previewRoadmap", () => api.previewRoadmap("BAD", "m"), "invalid project_id: BAD"],
    ["importRoadmap", () => api.importRoadmap("BAD", "m"), "invalid project_id: BAD"],
    ["proposeRoadmapReorder", () => api.proposeRoadmapReorder("BAD", []), "invalid project_id: BAD"],
    ["advanceProjectRoadmap", () => api.advanceProjectRoadmap("BAD"), "invalid project_id: BAD"],
    [
      "getRoadmapNextItemProposal",
      () => api.getRoadmapNextItemProposal("BAD"),
      "invalid project_id: BAD",
    ],
    ["proposeRoadmapEdit", () => api.proposeRoadmapEdit("BAD", "itm_1", {}), "invalid project_id: BAD"],
    ["proposeRoadmapEdit", () => api.proposeRoadmapEdit(PROJECT, "bad id", {}), "invalid item_id: bad id"],
  ] as const)("%s rejects an unsafe segment before any request is built", async (_label, call, message) => {
    const seen = serve("all", "/api/*", json({}));
    await expect(call()).rejects.toThrow(message);
    expect(seen).toHaveLength(0);
  });
});

describe("project error mapping", () => {
  it("carries the status and string detail of a 404", async () => {
    server.use(
      http.get(PROJECT_PATH, () => HttpResponse.json({ detail: "project not found" }, { status: 404 })),
    );
    const err = await rejection(() => api.getProject(PROJECT));
    expect(err.status).toBe(404);
    expect(err.message).toBe("404: project not found");
  });

  it("deleteProject surfaces a structured detail as code and reason", async () => {
    server.use(
      http.delete(PROJECT_PATH, () =>
        HttpResponse.json(
          { detail: { detail: "project_has_workspaces", reason: "2 workspaces" } },
          { status: 409 },
        ),
      ),
    );
    const err = await rejection(() => api.deleteProject(PROJECT));
    expect(err.message).toBe("409: project_has_workspaces: 2 workspaces");
  });

  it("falls back to the status text when the error body is empty", async () => {
    server.use(
      http.post(`${PROJECT_PATH}/complete`, () =>
        HttpResponse.text("", { status: 409, statusText: "Conflict" }),
      ),
    );
    const err = await rejection(() => api.completeProject(PROJECT));
    expect(err.detail).toBe("Conflict");
  });

  it("falls back to the status text for a validation error's list-shaped detail", async () => {
    server.use(
      http.post(PROJECTS, () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "title"], msg: "Field required" }] },
          { status: 422, statusText: "Unprocessable Entity" },
        ),
      ),
    );
    const err = await rejection(() => api.createProject("T", null));
    expect(err.detail).toBe("Unprocessable Entity");
  });

  it("surfaces a non-JSON error body as raw text", async () => {
    server.use(
      http.post(`${ROADMAP_PATH}/advance`, () =>
        HttpResponse.text("upstream exploded", { status: 502, statusText: "Bad Gateway" }),
      ),
    );
    const err = await rejection(() => api.advanceProjectRoadmap(PROJECT));
    expect(err.detail).toBe("upstream exploded");
  });
});
