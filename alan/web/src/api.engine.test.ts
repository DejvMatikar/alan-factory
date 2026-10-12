import { HttpResponse, http, type JsonBodyType } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, ConfigProposalBlockedError, DependencySweepBlockedError, api } from "./api";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { buildStep2Payload, seedDraftFromLive } from "./features/connect/pages/connectStep2ConfigureLogic";
import {
  expectFullWireParity,
  makeConfiguredEngineConfig,
  makeEngineConfig,
  PROJECTION_EXCLUDED_FIELDS,
} from "./test/engineFixtures";
import { server } from "./test/server";

const TOKEN = "engine-token";
const KEY = "acme/widget";
const ENGINES = "/api/engines";
const ENGINE = `${ENGINES}/acme/widget`;
const PRESETS = "/api/engine-presets";

const CONFIG = makeEngineConfig({ posture: "propose" });
const FINDING = { check: "c", status: "fail", message: "blocked", remedy: "fix it", remedy_url: "" } as const;
const PREFLIGHT = {
  slug: KEY,
  checked_at: "2026-01-01T00:00:00Z",
  overall: "fail",
  findings: [FINDING],
} as const;
const QUIESCE = {
  slug: KEY,
  residue_workspace_ids: ["ws-1"],
  workers_interrupted: 1,
  chats_cancelled: 0,
  pool_disposed: false,
  pool_residue: "still leased",
};
const POOL = {
  slug: KEY,
  size: 2,
  free_count: 1,
  leased_count: 1,
  warm: true,
  quiescing: false,
  disk_usage_bytes: 10,
  warmup_seconds: null,
};
const SOURCE = {
  slug: KEY,
  in_version_control: true,
  config_path: "config/engines.d/widget.yaml",
  posture_ceiling: "propose",
  export_command: null,
};
const ONBOARDING_STATE = {
  slug: KEY,
  stack: "python",
  scaffold_pr_number: 7,
  scaffold_pr_url: "https://example.test/pr/7",
  authoritative_checks: ["CI"],
  protection_applied_keys: [],
  protection_deferred: [],
};
const READINESS = { state: ONBOARDING_STATE, checklist: [FINDING], overall: "fail" };
const SWEEP_STATUS = { slug: KEY, enabled: true, last_run_at: null, next_run_at: "2026-02-01T00:00:00Z" };
const AGENT_VALUE = { value: "sonnet", display: "sonnet", source: "builtin", source_detail: null };
const AGENT_CATALOGUE = {
  engine: KEY,
  notes: { appended_prompt: "Appended to Claude Code's own prompt.", inherited: [] },
  entries: [
    {
      id: "team_role:implementation/implementer",
      name: "Implementer",
      kind: "team_role",
      scope: "repo",
      when_runs: "when an implementation task starts",
      model: AGENT_VALUE,
      effort: AGENT_VALUE,
      prompt_overridden: false,
      turn_limit: { max_turns: 40, adaptive: false },
      session: { behaviour: "new", detail: "starts a new session" },
      working_directory: "the leased checkout",
      timeouts: {},
      tools: {},
      process: { detached: false },
    },
  ],
};
const AGENT_SUMMARY = {
  engine: KEY,
  total: 1,
  by_model: [{ name: "sonnet", count: 1 }],
  by_effort: [{ name: "high", count: 1 }],
  by_kind: [{ name: "team_role", count: 1 }],
  warnings: 0,
  infos: 0,
  errors: 0,
};

type Verb = "get" | "post" | "put" | "patch" | "all";

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

function json(body: JsonBodyType, status = 200): () => Response {
  return () => HttpResponse.json(body, { status });
}

async function rejection(call: () => Promise<unknown>): Promise<unknown> {
  try {
    await call();
  } catch (err) {
    return err;
  }
  throw new Error("expected a rejection");
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
    label: "setEnginePosture",
    verb: "post",
    path: `${ENGINE}/posture`,
    respond: json(CONFIG),
    call: () => api.setEnginePosture(KEY, "propose"),
    body: JSON.stringify({ posture: "propose", confirm: true }),
  },
  {
    label: "setEngineAccent",
    verb: "put",
    path: `${ENGINE}/accent`,
    respond: json(CONFIG),
    call: () => api.setEngineAccent(KEY, { color: "plum", intensity: "bold" }),
    body: JSON.stringify({ accent: { color: "plum", intensity: "bold" } }),
  },
  {
    label: "hideEngine",
    verb: "post",
    path: `${ENGINE}/hide`,
    respond: json(QUIESCE),
    call: () => api.hideEngine(KEY),
    body: "",
  },
  {
    label: "unhideEngine",
    verb: "post",
    path: `${ENGINE}/unhide`,
    respond: json(CONFIG),
    call: () => api.unhideEngine(KEY),
    body: "",
  },
  {
    label: "createEngine",
    verb: "post",
    path: ENGINES,
    respond: json(CONFIG, 201),
    call: () => api.createEngine(CONFIG),
    body: JSON.stringify(CONFIG),
  },
  {
    label: "updateEngineConfig",
    verb: "patch",
    path: ENGINE,
    respond: json(CONFIG),
    call: () => api.updateEngineConfig(KEY, CONFIG),
    body: JSON.stringify(CONFIG),
  },
  {
    label: "runEnginePreflight",
    verb: "post",
    path: `${ENGINE}/preflight`,
    respond: json(PREFLIGHT),
    call: () => api.runEnginePreflight(KEY),
    body: "",
  },
  {
    label: "validateEngineConfigDraft",
    verb: "post",
    path: `${ENGINE}/config-draft-validate`,
    respond: json(FINDING),
    call: () => api.validateEngineConfigDraft(KEY, CONFIG),
    body: JSON.stringify(CONFIG),
  },
  {
    label: "scaffoldEngine",
    verb: "post",
    path: `${ENGINE}/scaffold`,
    respond: json(ONBOARDING_STATE),
    call: () => api.scaffoldEngine(KEY),
    body: "",
  },
  {
    label: "protectEngine",
    verb: "post",
    path: `${ENGINE}/protect`,
    respond: json(ONBOARDING_STATE),
    call: () => api.protectEngine(KEY),
    body: "",
  },
];

describe("engine mutations send the control token through the shared client", () => {
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

  it("setEngineAccent sends a null accent to clear the choice", async () => {
    const seen = serve("put", `${ENGINE}/accent`, json(CONFIG));
    await api.setEngineAccent(KEY, null);
    expect(seen[0]?.body).toBe(JSON.stringify({ accent: null }));
  });

  it("hideEngine returns the quiesce report unchanged", async () => {
    serve("post", `${ENGINE}/hide`, json(QUIESCE));
    expect(await api.hideEngine(KEY)).toEqual(QUIESCE);
  });

  it("unhideEngine returns the full configuration with the hidden flag", async () => {
    serve("post", `${ENGINE}/unhide`, json(makeEngineConfig({ hidden: false })));
    const unhidden = await api.unhideEngine(KEY);
    expect(unhidden.hidden).toBe(false);
    expect(unhidden).toEqual(makeEngineConfig({ hidden: false }));
  });
});

interface ReadCase {
  label: string;
  path: string;
  payload: JsonBodyType;
  call: () => Promise<unknown>;
  result: unknown;
  search: string;
}

const READS: ReadCase[] = [
  {
    label: "listEngines",
    path: ENGINES,
    payload: { engines: [CONFIG] },
    call: () => api.listEngines(),
    result: [CONFIG],
    search: "",
  },
  { label: "getEngine", path: ENGINE, payload: CONFIG, call: () => api.getEngine(KEY), result: CONFIG, search: "" },
  {
    label: "getEngineConfig",
    path: ENGINE,
    payload: CONFIG,
    call: () => api.getEngineConfig(KEY),
    result: CONFIG,
    search: "",
  },
  {
    label: "getEngineSources",
    path: `${ENGINES}/sources`,
    payload: { sources: [SOURCE] },
    call: () => api.getEngineSources(),
    result: [SOURCE],
    search: "",
  },
  {
    label: "getEnginePool",
    path: `${ENGINE}/pool`,
    payload: POOL,
    call: () => api.getEnginePool(KEY),
    result: POOL,
    search: "",
  },
  {
    label: "getEnginePreflight",
    path: `${ENGINE}/preflight`,
    payload: PREFLIGHT,
    call: () => api.getEnginePreflight(KEY),
    result: PREFLIGHT,
    search: "",
  },
  {
    label: "getEngineDependencySweep",
    path: `${ENGINE}/dependency-sweep`,
    payload: SWEEP_STATUS,
    call: () => api.getEngineDependencySweep(KEY),
    result: SWEEP_STATUS,
    search: "",
  },
  {
    label: "getEngineOnboarding",
    path: `${ENGINE}/onboarding`,
    payload: READINESS,
    call: () => api.getEngineOnboarding(KEY),
    result: READINESS,
    search: "",
  },
  {
    label: "listEnginePresets",
    path: PRESETS,
    payload: { presets: [{ name: "python", config: CONFIG }] },
    call: () => api.listEnginePresets(),
    result: [{ name: "python", config: CONFIG }],
    search: "",
  },
  {
    label: "getEnginePresetConfig",
    path: `${PRESETS}/python/config`,
    payload: CONFIG,
    call: () => api.getEnginePresetConfig("python", "acme", "widget"),
    result: CONFIG,
    search: "?owner=acme&repo=widget",
  },
  {
    label: "getEngineAgents",
    path: `${ENGINE}/agents`,
    payload: AGENT_CATALOGUE,
    call: () => api.getEngineAgents(KEY),
    result: AGENT_CATALOGUE,
    search: "",
  },
  {
    label: "getEngineAgentsSummary",
    path: `${ENGINE}/agents/summary`,
    payload: AGENT_SUMMARY,
    call: () => api.getEngineAgentsSummary(KEY),
    result: AGENT_SUMMARY,
    search: "",
  },
];

describe("engine reads never send the control token", () => {
  it.each(READS)("$label omits it, sends the unchanged query and returns the parsed body", async (read) => {
    const seen = serve("get", read.path, json(read.payload));
    expect(await read.call()).toEqual(read.result);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ method: "GET", pathname: read.path, search: read.search, token: null });
  });

  it("listEngines returns the engines unmapped, keeping the hidden flag and every other wire key", async () => {
    const hidden = makeEngineConfig({ hidden: true, accent: { color: "rose", intensity: "soft" } });
    serve("get", ENGINES, json({ engines: [hidden] }));
    const [engine] = await api.listEngines();
    expect(engine).toEqual(hidden);
    expect(engine?.hidden).toBe(true);
  });

  it("getEnginePreflight returns null when the server has no result yet", async () => {
    serve("get", `${ENGINE}/preflight`, json(null));
    expect(await api.getEnginePreflight(KEY)).toBeNull();
  });

  it("getEnginePresetConfig refuses a preset name whose encoded path is unsafe before any request", async () => {
    const seen = serve("all", "/api/*", json({}));
    await expect(api.getEnginePresetConfig("bad name", "acme", "widget")).rejects.toThrow(
      "unsafe request path: /api/engine-presets/bad%20name/config",
    );
    expect(seen).toHaveLength(0);
  });
});

const KEYED_CALLS: [string, (key: string) => Promise<unknown>][] = [
  ["getEngine", (key) => api.getEngine(key)],
  ["getEngineConfig", (key) => api.getEngineConfig(key)],
  ["getEnginePool", (key) => api.getEnginePool(key)],
  ["getEnginePreflight", (key) => api.getEnginePreflight(key)],
  ["getEngineDependencySweep", (key) => api.getEngineDependencySweep(key)],
  ["getEngineOnboarding", (key) => api.getEngineOnboarding(key)],
  ["getEngineAgents", (key) => api.getEngineAgents(key)],
  ["getEngineAgentsSummary", (key) => api.getEngineAgentsSummary(key)],
  ["setEnginePosture", (key) => api.setEnginePosture(key, "observe")],
  ["setEngineAccent", (key) => api.setEngineAccent(key, null)],
  ["hideEngine", (key) => api.hideEngine(key)],
  ["unhideEngine", (key) => api.unhideEngine(key)],
  ["updateEngineConfig", (key) => api.updateEngineConfig(key, CONFIG)],
  ["runEnginePreflight", (key) => api.runEnginePreflight(key)],
  ["validateEngineConfigDraft", (key) => api.validateEngineConfigDraft(key, CONFIG)],
  ["scaffoldEngine", (key) => api.scaffoldEngine(key)],
  ["protectEngine", (key) => api.protectEngine(key)],
  ["setEngineDependencySweepEnabled", (key) => api.setEngineDependencySweepEnabled(key, true)],
  ["proposeEngineConfig", (key) => api.proposeEngineConfig(key)],
];

describe("engine path segment guards", () => {
  it.each(KEYED_CALLS)("%s rejects an unsafe owner before any request is built", async (_label, call) => {
    const seen = serve("all", "/api/*", json({}));
    await expect(call("bad owner/widget")).rejects.toThrow("invalid engine_owner: bad owner");
    expect(seen).toHaveLength(0);
  });

  it.each(KEYED_CALLS)("%s rejects an unsafe repo before any request is built", async (_label, call) => {
    const seen = serve("all", "/api/*", json({}));
    await expect(call("acme/bad repo")).rejects.toThrow("invalid engine_repo: bad repo");
    expect(seen).toHaveLength(0);
  });

  it("an engine key with no slash is rejected as an empty owner", async () => {
    const seen = serve("all", "/api/*", json({}));
    await expect(api.getEngine("acme")).rejects.toThrow("invalid engine_owner: ");
    expect(seen).toHaveLength(0);
  });
});

describe("engine error mapping", () => {
  it("carries the status and string detail of a 404", async () => {
    server.use(http.get(ENGINE, () => HttpResponse.json({ detail: "engine not found" }, { status: 404 })));
    const err = await rejection(() => api.getEngine(KEY));
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("404: engine not found");
  });

  it("surfaces a structured detail as code and reason on a mutation", async () => {
    server.use(
      http.post(`${ENGINE}/hide`, () =>
        HttpResponse.json({ detail: { detail: "engine_is_default", reason: "cannot hide it" } }, { status: 409 }),
      ),
    );
    const err = await rejection(() => api.hideEngine(KEY));
    expect((err as ApiError).message).toBe("409: engine_is_default: cannot hide it");
  });

  it("falls back to the status text when the error body is empty", async () => {
    server.use(http.post(`${ENGINE}/scaffold`, () => HttpResponse.text("", { status: 502, statusText: "Bad Gateway" })));
    const err = await rejection(() => api.scaffoldEngine(KEY));
    expect((err as ApiError).detail).toBe("Bad Gateway");
  });

  it("surfaces a non-JSON error body as raw text", async () => {
    server.use(http.get(`${ENGINE}/pool`, () => HttpResponse.text("upstream exploded", { status: 502 })));
    const err = await rejection(() => api.getEnginePool(KEY));
    expect((err as ApiError).detail).toBe("upstream exploded");
  });

  it("falls back to the status text for a validation error's list-shaped detail", async () => {
    server.use(
      http.post(ENGINES, () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "repo"], msg: "Field required" }] },
          { status: 422, statusText: "Unprocessable Entity" },
        ),
      ),
    );
    const err = await rejection(() => api.createEngine(CONFIG));
    expect((err as ApiError).detail).toBe("Unprocessable Entity");
  });
});

describe("the dependency-sweep toggle keeps its bespoke request and attaches the token by hand", () => {
  it("sends the stored token with the enabled flag", async () => {
    const seen = serve("post", `${ENGINE}/dependency-sweep`, json(CONFIG));
    await api.setEngineDependencySweepEnabled(KEY, true);
    expect(seen[0]).toMatchObject({ token: TOKEN, body: JSON.stringify({ enabled: true }) });
  });

  it("omits the token when none is stored", async () => {
    clearControlToken();
    const seen = serve("post", `${ENGINE}/dependency-sweep`, json(CONFIG));
    await api.setEngineDependencySweepEnabled(KEY, false);
    expect(seen[0]?.token).toBeNull();
  });

  it("maps a 422 finding body to DependencySweepBlockedError", async () => {
    serve("post", `${ENGINE}/dependency-sweep`, json({ detail: FINDING }, 422));
    const err = await rejection(() => api.setEngineDependencySweepEnabled(KEY, true));
    expect(err).toBeInstanceOf(DependencySweepBlockedError);
    expect((err as DependencySweepBlockedError).finding).toEqual(FINDING);
  });

  it("keeps a 422 without a finding as a plain status-and-detail error", async () => {
    serve("post", `${ENGINE}/dependency-sweep`, json({ detail: "not a finding" }, 422));
    const err = await rejection(() => api.setEngineDependencySweepEnabled(KEY, true));
    expect(err).not.toBeInstanceOf(DependencySweepBlockedError);
    expect((err as Error).message).toBe("422: not a finding");
  });
});

describe("the config proposal keeps its bespoke request and attaches the token by hand", () => {
  const PROPOSAL = {
    slug: KEY,
    base_preset: "python",
    detected_stacks: ["python"],
    additional_stacks: [],
    proposed_config: CONFIG,
    diff: [],
    validation: FINDING,
  };

  it("sends the stored token with the base preset", async () => {
    const seen = serve("post", `${ENGINE}/config-proposal`, json(PROPOSAL));
    await api.proposeEngineConfig(KEY, "python");
    expect(seen[0]).toMatchObject({ token: TOKEN, body: JSON.stringify({ base_preset: "python" }) });
  });

  it("sends a null base preset by default and omits the token when none is stored", async () => {
    clearControlToken();
    const seen = serve("post", `${ENGINE}/config-proposal`, json(PROPOSAL));
    await api.proposeEngineConfig(KEY);
    expect(seen[0]).toMatchObject({ token: null, body: JSON.stringify({ base_preset: null }) });
  });

  it("maps a 422 finding body to ConfigProposalBlockedError", async () => {
    serve("post", `${ENGINE}/config-proposal`, json({ detail: FINDING }, 422));
    const err = await rejection(() => api.proposeEngineConfig(KEY));
    expect(err).toBeInstanceOf(ConfigProposalBlockedError);
    expect((err as ConfigProposalBlockedError).finding).toEqual(FINDING);
  });

  it("keeps any other failure as an ApiError with the status and detail", async () => {
    serve("post", `${ENGINE}/config-proposal`, json({ detail: "boom" }, 500));
    const err = await rejection(() => api.proposeEngineConfig(KEY));
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("500: boom");
  });
});

describe("a configuration write never drops a key the read supplied", () => {
  const LIVE = makeConfiguredEngineConfig({
    posture: "self_merge",
    hidden: true,
    hidden_restore_posture: "propose",
    accent: { color: "plum", intensity: "bold" },
    prompt_overrides: { "implementation/implementer": { text: "pinned", base_fingerprint: "fp" } },
    dependency_sweep: { enabled: true, cadence_seconds: 3600, enable_python_vuln_scan: true },
    unblock_enabled: false,
    cache_env: { UV_CACHE_DIR: "uv" },
    scenario: { command_prefixes: [["uv", "run"]], timeout_s: 30, aggregate_budget_s: 90 },
  });

  function sentBody(seen: SeenRequest[]): Record<string, unknown> {
    return JSON.parse(seen[0]?.body ?? "{}") as Record<string, unknown>;
  }

  it("keeps every key of the full-wire configuration on the PATCH built by the wizard save", async () => {
    const seen = serve("patch", ENGINE, json(LIVE));
    await api.updateEngineConfig(KEY, buildStep2Payload(LIVE, seedDraftFromLive(LIVE)));
    expectFullWireParity(sentBody(seen), LIVE);
  });

  it.each(PROJECTION_EXCLUDED_FIELDS)("carries %s at live's non-default value on the wizard save PATCH", async (field) => {
    const seen = serve("patch", ENGINE, json(LIVE));
    await api.updateEngineConfig(KEY, buildStep2Payload(LIVE, seedDraftFromLive(LIVE)));
    expect(sentBody(seen)[field]).toEqual(LIVE[field]);
  });

  it("keeps every key of a preset-shaped baseline on the create POST", async () => {
    const preset = makeConfiguredEngineConfig({ repo: { owner: "new", repo: "repo" }, preset: "python" });
    serve("get", `${PRESETS}/python/config`, json(preset));
    const seen = serve("post", ENGINES, json(preset, 201));
    const baseline = await api.getEnginePresetConfig("python", "new", "repo");
    await api.createEngine(baseline);
    expectFullWireParity(sentBody(seen), preset);
  });

  it.each(PROJECTION_EXCLUDED_FIELDS)("carries the preset's %s on the create POST", async (field) => {
    const preset = makeConfiguredEngineConfig({ repo: { owner: "new", repo: "repo" }, preset: "python" });
    serve("get", `${PRESETS}/python/config`, json(preset));
    const seen = serve("post", ENGINES, json(preset, 201));
    await api.createEngine(await api.getEnginePresetConfig("python", "new", "repo"));
    expect(sentBody(seen)[field]).toEqual(preset[field]);
  });

  it("sends the six fields the earlier hand-written type never declared on the create POST", async () => {
    const seen = serve("post", ENGINES, json(LIVE, 201));
    await api.createEngine(LIVE);
    expect(Object.keys(sentBody(seen))).toEqual(
      expect.arrayContaining(["hidden", "hidden_restore_posture", "accent", "unblock_enabled", "scenario", "cache_env"]),
    );
  });
});
