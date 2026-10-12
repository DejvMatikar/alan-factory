import { HttpResponse, http, type JsonBodyType } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, api } from "./api";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { server } from "./test/server";

const TOKEN = "my-secret-token";
const LOOP = "alan-issue-driver";

interface SeenRequest {
  method: string;
  pathname: string;
  search: string;
  token: string | null;
  contentType: string | null;
}

function serve(verb: "get" | "post", path: string, body: JsonBodyType): SeenRequest[] {
  const seen: SeenRequest[] = [];
  server.use(
    http[verb](path, ({ request }) => {
      const url = new URL(request.url);
      seen.push({
        method: request.method,
        pathname: url.pathname,
        search: url.search,
        token: request.headers.get(CONTROL_TOKEN_HEADER),
        contentType: request.headers.get("Content-Type"),
      });
      return HttpResponse.json(body);
    }),
  );
  return seen;
}

const LOOP_STATUS = {
  name: LOOP,
  enabled: true,
  dedicated_clone: true,
  paused: false,
  decision: { action: "spawn", reason: null, detail: null, next_wake_at: null },
  last_outcome: null,
  final_message: "",
  denial_count: 0,
  run_now_pending: true,
};

interface ReadCase {
  label: string;
  path: string;
  body: JsonBodyType;
  call: () => Promise<unknown>;
}

const READS: ReadCase[] = [
  {
    label: "getHealth",
    path: "/api/health",
    body: { ok: true, queue_paused: false, paused_until: null },
    call: () => api.getHealth(),
  },
  {
    label: "getHealthRepos",
    path: "/api/health/repos",
    body: { computed_at: null, rows: [] },
    call: () => api.getHealthRepos(),
  },
  {
    label: "getHealthDigest",
    path: "/api/health/digest",
    body: { recovery: null, residue: null, digest_overdue: false },
    call: () => api.getHealthDigest(),
  },
  {
    label: "getUnblockerActivity",
    path: "/api/health/unblocker-activity",
    body: { scope: "fleet", events_total: 0 },
    call: () => api.getUnblockerActivity(null),
  },
  {
    label: "getDriverLoops",
    path: "/api/driver-loops",
    body: { supervised: true, loops: [] },
    call: () => api.getDriverLoops(),
  },
];

interface MutationCase {
  label: string;
  path: string;
  body: JsonBodyType;
  call: () => Promise<unknown>;
}

const MUTATIONS: MutationCase[] = [
  {
    label: "pauseDriverLoop",
    path: `/api/driver-loops/${LOOP}/pause`,
    body: { name: LOOP, paused: true },
    call: () => api.pauseDriverLoop(LOOP),
  },
  {
    label: "resumeDriverLoop",
    path: `/api/driver-loops/${LOOP}/resume`,
    body: { name: LOOP, paused: false },
    call: () => api.resumeDriverLoop(LOOP),
  },
  {
    label: "runDriverLoopNow",
    path: `/api/driver-loops/${LOOP}/run-now`,
    body: LOOP_STATUS,
    call: () => api.runDriverLoopNow(LOOP),
  },
  {
    label: "resumeQueue",
    path: "/api/queue/resume",
    body: { ok: true, queue_paused: false, paused_until: null },
    call: () => api.resumeQueue(),
  },
];

beforeEach(() => {
  clearControlToken();
});

describe("health reads", () => {
  it.each(READS)("$label issues a GET to $path and returns the parsed body", async (read) => {
    const seen = serve("get", read.path, read.body);
    const resp = await read.call();
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "GET", pathname: read.path, search: "" });
    expect(resp).toEqual(read.body);
  });

  it.each(READS)("$label never attaches the control token to a read", async (read) => {
    writeControlToken(TOKEN);
    const seen = serve("get", read.path, read.body);
    await read.call();
    expect(seen[0]?.token).toBeNull();
  });
});

describe("health mutations", () => {
  it.each(MUTATIONS)("$label issues a POST to $path and returns the parsed body", async (mutation) => {
    const seen = serve("post", mutation.path, mutation.body);
    const resp = await mutation.call();
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "POST", pathname: mutation.path, search: "" });
    expect(resp).toEqual(mutation.body);
  });

  it.each(MUTATIONS)("$label attaches the control token when one is stored", async (mutation) => {
    writeControlToken(TOKEN);
    const seen = serve("post", mutation.path, mutation.body);
    await mutation.call();
    expect(seen[0]?.token).toBe(TOKEN);
  });

  it.each(MUTATIONS)("$label omits the control token when none is stored", async (mutation) => {
    const seen = serve("post", mutation.path, mutation.body);
    await mutation.call();
    expect(seen[0]?.token).toBeNull();
  });

  it.each(MUTATIONS)("$label sends no JSON content type, having no body", async (mutation) => {
    const seen = serve("post", mutation.path, mutation.body);
    await mutation.call();
    expect(seen[0]?.contentType).toBeNull();
  });
});

describe("driver loop names", () => {
  it.each([
    ["pauseDriverLoop", (name: string) => api.pauseDriverLoop(name)],
    ["resumeDriverLoop", (name: string) => api.resumeDriverLoop(name)],
    ["runDriverLoopNow", (name: string) => api.runDriverLoopNow(name)],
  ] as const)("%s rejects an unsafe name without issuing a request", async (_label, call) => {
    const seen = serve("post", "/api/driver-loops/*", LOOP_STATUS);
    await expect(call("Bad/Name")).rejects.toThrow("invalid name: Bad/Name");
    await expect(call("")).rejects.toThrow("invalid name: ");
    expect(seen).toHaveLength(0);
  });

  it("passes a valid hyphenated name through into the path", async () => {
    const seen = serve("post", "/api/driver-loops/multi-word-loop-2/pause", {
      name: "multi-word-loop-2",
      paused: true,
    });
    const ack = await api.pauseDriverLoop("multi-word-loop-2");
    expect(seen[0]?.pathname).toBe("/api/driver-loops/multi-word-loop-2/pause");
    expect(ack).toEqual({ name: "multi-word-loop-2", paused: true });
  });
});

async function rejection(call: () => Promise<unknown>): Promise<ApiError> {
  try {
    await call();
  } catch (err) {
    if (err instanceof ApiError) return err;
  }
  throw new Error("expected an ApiError rejection");
}

describe("health error mapping", () => {
  it("carries a string detail through", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const err = await rejection(() => api.getHealth());
    expect(err.status).toBe(500);
    expect(err.detail).toBe("boom");
    expect(err.message).toBe("500: boom");
  });

  it("flattens a structured detail into code and reason", async () => {
    server.use(
      http.post("/api/queue/resume", () =>
        HttpResponse.json({ detail: { detail: "token_mismatch", reason: "stale" } }, { status: 403 }),
      ),
    );
    const err = await rejection(() => api.resumeQueue());
    expect(err.status).toBe(403);
    expect(err.detail).toBe("token_mismatch: stale");
  });

  it("surfaces a 409 from run-now with the hold reason", async () => {
    server.use(
      http.post(`/api/driver-loops/${LOOP}/run-now`, () =>
        HttpResponse.json({ detail: "running" }, { status: 409 }),
      ),
    );
    const err = await rejection(() => api.runDriverLoopNow(LOOP));
    expect(err.status).toBe(409);
    expect(err.detail).toBe("running");
  });

  it("falls back to the status text when the JSON body has no detail", async () => {
    server.use(
      http.get("/api/health/repos", () =>
        HttpResponse.json({}, { status: 502, statusText: "Bad Gateway" }),
      ),
    );
    const err = await rejection(() => api.getHealthRepos());
    expect(err.detail).toBe("Bad Gateway");
  });

  it("falls back to the status text when the detail is an empty string", async () => {
    server.use(
      http.get("/api/health/repos", () =>
        HttpResponse.json({ detail: "" }, { status: 502, statusText: "Bad Gateway" }),
      ),
    );
    const err = await rejection(() => api.getHealthRepos());
    expect(err.detail).toBe("Bad Gateway");
  });

  it.each([
    ["JSON null", "null"],
    ["a JSON number", "42"],
  ])("falls back to the status text when the error body is %s", async (_label, raw) => {
    server.use(
      http.get("/api/health/digest", () =>
        HttpResponse.text(raw, {
          status: 500,
          statusText: "Internal Server Error",
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const err = await rejection(() => api.getHealthDigest());
    expect(err.detail).toBe("Internal Server Error");
  });

  it("uses the raw text when the error body is not JSON", async () => {
    server.use(
      http.get("/api/driver-loops", () =>
        HttpResponse.text("upstream exploded", { status: 500, statusText: "Internal Server Error" }),
      ),
    );
    const err = await rejection(() => api.getDriverLoops());
    expect(err.detail).toBe("upstream exploded");
  });

  it("falls back to the status text when the error body is empty", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.text("", { status: 503, statusText: "Service Unavailable" }),
      ),
    );
    const err = await rejection(() => api.getHealth());
    expect(err.message).toBe("503: Service Unavailable");
  });
});
