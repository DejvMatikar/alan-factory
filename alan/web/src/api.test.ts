import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  classifyEngineConfigFinding,
  composeClarifications,
  deriveAssumptions,
  deriveBuildTitle,
  parseSseChunk,
  ApiError,
  ArchivedSessionRedirect,
  CHANGE_KINDS,
  ConfigProposalBlockedError,
  DependencySweepBlockedError,
  type CounterCase,
  type ParsedSseEvent,
  type Task,
  type TaskProposal,
  type TaskStatus,
} from "./api";
import { CONTROL_TOKEN_HEADER, writeControlToken } from "./controlToken";

import { makeEngineConfig } from "./test/engineFixtures";
import { server } from "./test/server";
import { makeWorkspaceTask } from "./test/workspaceFixtures";

describe("deriveBuildTitle", () => {
  it("uses the first non-empty line", () => {
    expect(deriveBuildTitle("Add a widget\nmore text")).toBe("Add a widget");
  });

  it("skips leading blank lines and strips a markdown heading", () => {
    expect(deriveBuildTitle("\n\n#  My Build\nbody")).toBe("My Build");
  });

  it("falls back to 'build' when the content is empty", () => {
    expect(deriveBuildTitle("   \n  ")).toBe("build");
  });

  it("slices the title to 50 characters", () => {
    const long = "x".repeat(80);
    expect(deriveBuildTitle(long)).toHaveLength(50);
  });
});

describe("composeClarifications", () => {
  it("appends a Clarifications section with each Q/A", () => {
    const out = composeClarifications("Brief body", [
      { question: "Which DB?", answer: "  Postgres  " },
    ]);
    expect(out).toContain("Brief body");
    expect(out).toContain("## Clarifications");
    expect(out).toContain("### Which DB?");
    expect(out).toContain("Postgres");
  });
});

describe("deriveAssumptions", () => {
  it("returns null for a null or undefined backend value", () => {
    expect(deriveAssumptions(null)).toBeNull();
    expect(deriveAssumptions(undefined)).toBeNull();
  });

  it("returns null for an empty list", () => {
    expect(deriveAssumptions([])).toBeNull();
  });

  it("returns null when every entry is blank", () => {
    expect(deriveAssumptions(["  ", ""])).toBeNull();
  });

  it("derives an open-status Assumption per raw string, filtering blanks", () => {
    expect(deriveAssumptions(["Assumed retries are safe", "  ", "Assumed idempotency"])).toEqual([
      { id: "asm-0", text: "Assumed retries are safe", status: "open" },
      { id: "asm-1", text: "Assumed idempotency", status: "open" },
    ]);
  });
});

describe("api.listEngines", () => {
  it("returns the engine catalog, including posture, from /api/engines", async () => {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            { repo: { owner: "DejvMatikar", repo: "alan" }, posture: "autonomous" },
            { repo: { owner: "sprocket-co", repo: "sprocket" }, posture: "propose" },
          ],
        }),
      ),
    );
    const engines = await api.listEngines();
    expect(engines.map((e) => `${e.repo.owner}/${e.repo.repo}`)).toEqual([
      "DejvMatikar/alan",
      "sprocket-co/sprocket",
    ]);
    expect(engines.map((e) => e.posture)).toEqual(["autonomous", "propose"]);
  });
});

describe("api.getEngineSources", () => {
  it("returns per-engine version-control provenance from /api/engines/sources", async () => {
    server.use(
      http.get("/api/engines/sources", () =>
        HttpResponse.json({
          sources: [
            {
              slug: "acme/widget",
              in_version_control: true,
              config_path: "config/engines.d/widget.yaml",
              posture_ceiling: "propose",
              export_command: null,
            },
            {
              slug: "acme/other",
              in_version_control: false,
              config_path: null,
              posture_ceiling: "autonomous",
              export_command: "uv run python scripts/export_engine_configs.py acme/other",
            },
          ],
        }),
      ),
    );
    const sources = await api.getEngineSources();
    expect(sources.map((s) => s.slug)).toEqual(["acme/widget", "acme/other"]);
    expect(sources[0]).toMatchObject({
      in_version_control: true,
      config_path: "config/engines.d/widget.yaml",
    });
    expect(sources[1]).toMatchObject({ in_version_control: false, config_path: null });
  });
});

describe("engine ops (posture/pool/preflight)", () => {
  it("includes 'engines' in CHANGE_KINDS", () => {
    expect(CHANGE_KINDS).toContain("engines");
  });

  it("api.getEngine fetches identity + posture from /api/engines/{owner}/{repo}", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () =>
        HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "propose" }),
      ),
    );
    const detail = await api.getEngine("acme/widget");
    expect(detail).toEqual({ repo: { owner: "acme", repo: "widget" }, posture: "propose" });
  });

  it("api.getEngine rejects an engine key with no slash", async () => {
    await expect(api.getEngine("no-slash")).rejects.toThrow();
  });

  it("api.getEnginePool fetches warmth/free/leased counts from the pool sub-resource", async () => {
    server.use(
      http.get("/api/engines/acme/widget/pool", () =>
        HttpResponse.json({
          slug: "acme/widget",
          size: 3,
          free_count: 2,
          leased_count: 1,
          warm: true,
          quiescing: false,
          disk_usage_bytes: 1024,
        }),
      ),
    );
    const pool = await api.getEnginePool("acme/widget");
    expect(pool.warm).toBe(true);
    expect(pool.free_count).toBe(2);
    expect(pool.leased_count).toBe(1);
  });

  it("api.getEnginePreflight returns null when preflight has never run", async () => {
    server.use(
      http.get("/api/engines/acme/widget/preflight", () => HttpResponse.json(null)),
    );
    const result = await api.getEnginePreflight("acme/widget");
    expect(result).toBeNull();
  });

  it("api.getEnginePreflight returns the last preflight outcome", async () => {
    server.use(
      http.get("/api/engines/acme/widget/preflight", () =>
        HttpResponse.json({
          slug: "acme/widget",
          checked_at: "2026-07-20T00:00:00Z",
          overall: "pass",
          findings: [{ check: "repo_clonable", status: "pass", message: "ok", remedy: "" }],
        }),
      ),
    );
    const result = await api.getEnginePreflight("acme/widget");
    expect(result?.overall).toBe("pass");
    expect(result?.findings).toHaveLength(1);
  });

  it("api.getRepoStatus fetches the per-repo rollup from the status sub-resource", async () => {
    const unknown = { status: "unknown" as const, detail: "no credential available for this repo's forge" };
    server.use(
      http.get("/api/engines/acme/widget/status", () =>
        HttpResponse.json({
          slug: "acme/widget",
          is_default: false,
          main_ci: { provenance: unknown, green_count: null, red_count: null, counted_over: null },
          open_prs: { provenance: unknown, total: null, authored_by_alan: null },
          spend: { provenance: unknown, state: null, total_budget_usd: null, total_burn_usd: null },
          last_deploy: { provenance: unknown, outcome: null, pr_number: null, at: null },
          health: { provenance: unknown, finding_count: null, verdict: null },
        }),
      ),
    );
    const status = await api.getRepoStatus("acme/widget");
    expect(status.slug).toBe("acme/widget");
    expect(status.is_default).toBe(false);
    expect(status.main_ci.provenance.status).toBe("unknown");
  });

  it("api.setEnginePosture posts the target posture with confirm=true", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/engines/acme/widget/posture", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" });
      }),
    );
    const updated = await api.setEnginePosture("acme/widget", "autonomous");
    expect(captured).toEqual({ posture: "autonomous", confirm: true });
    expect(updated.posture).toBe("autonomous");
  });

  it("api.setEngineAccent PUTs the chosen colour and intensity", async () => {
    let captured: unknown = null;
    let method = "";
    server.use(
      http.put("/api/engines/acme/widget/accent", async ({ request }) => {
        method = request.method;
        captured = await request.json();
        return HttpResponse.json({
          repo: { owner: "acme", repo: "widget" },
          posture: "autonomous",
          accent: { color: "ruby", intensity: "bold" },
        });
      }),
    );
    const updated = await api.setEngineAccent("acme/widget", { color: "ruby", intensity: "bold" });
    expect(method).toBe("PUT");
    expect(captured).toEqual({ accent: { color: "ruby", intensity: "bold" } });
    expect(updated.accent).toEqual({ color: "ruby", intensity: "bold" });
  });

  it("api.setEngineAccent PUTs an explicit null to reset to automatic assignment", async () => {
    let captured: unknown = null;
    server.use(
      http.put("/api/engines/acme/widget/accent", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "autonomous", accent: null });
      }),
    );
    const updated = await api.setEngineAccent("acme/widget", null);
    expect(captured).toEqual({ accent: null });
    expect(updated.accent).toBeNull();
  });

  it("api.listEnginePresets returns the preset catalog from /api/engine-presets", async () => {
    server.use(
      http.get("/api/engine-presets", () =>
        HttpResponse.json({
          presets: [
            { name: "node", config: { repo: { owner: "_preset", repo: "node" } } },
            { name: "python", config: { repo: { owner: "_preset", repo: "python" } } },
          ],
        }),
      ),
    );
    const presets = await api.listEnginePresets();
    expect(presets.map((p) => p.name)).toEqual(["node", "python"]);
  });
});

describe("engine ops (dependency sweep)", () => {
  it("api.getEngineDependencySweep fetches enabled/last-run/next-run from the sub-resource", async () => {
    server.use(
      http.get("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json({
          slug: "acme/widget",
          enabled: true,
          last_run_at: "2026-07-20T00:00:00Z",
          next_run_at: "2026-07-27T00:00:00Z",
        }),
      ),
    );
    const status = await api.getEngineDependencySweep("acme/widget");
    expect(status).toEqual({
      slug: "acme/widget",
      enabled: true,
      last_run_at: "2026-07-20T00:00:00Z",
      next_run_at: "2026-07-27T00:00:00Z",
    });
  });

  it("api.setEngineDependencySweepEnabled posts {enabled} and returns the persisted config", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          repo: { owner: "acme", repo: "widget" },
          dependency_sweep: { enabled: true, cadence_seconds: 604800, enable_python_vuln_scan: false },
        });
      }),
    );
    const updated = await api.setEngineDependencySweepEnabled("acme/widget", true);
    expect(captured).toEqual({ enabled: true });
    expect(updated.dependency_sweep).toEqual({
      enabled: true,
      cadence_seconds: 604800,
      enable_python_vuln_scan: false,
    });
  });

  it("throws DependencySweepBlockedError carrying the Finding when refused with 422", async () => {
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json(
          {
            detail: {
              check: "dependabot_config_present",
              status: "fail",
              message: ".github/dependabot.yml not found in acme/widget",
              remedy: "add a .github/dependabot.yml file to the repository",
            },
          },
          { status: 422 },
        ),
      ),
    );
    let caught: DependencySweepBlockedError | null = null;
    try {
      await api.setEngineDependencySweepEnabled("acme/widget", true);
    } catch (err) {
      if (err instanceof DependencySweepBlockedError) caught = err;
    }
    expect(caught?.finding.check).toBe("dependabot_config_present");
    expect(caught?.finding.status).toBe("fail");
    expect(caught?.finding.message).toContain("dependabot.yml");
    expect(caught?.finding.remedy).toContain("add a");
  });

  it("falls back to a generic error when a 422 body is not JSON", async () => {
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.text("not json", { status: 422 }),
      ),
    );
    const call = api.setEngineDependencySweepEnabled("acme/widget", true);
    await expect(call).rejects.not.toBeInstanceOf(DependencySweepBlockedError);
    await expect(call).rejects.toThrow("422: not json");
  });

  it("falls back to a generic error when a 422 body's top-level JSON value is not an object", async () => {
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json(42, { status: 422 }),
      ),
    );
    const call = api.setEngineDependencySweepEnabled("acme/widget", true);
    await expect(call).rejects.not.toBeInstanceOf(DependencySweepBlockedError);
  });

  it("falls back to a generic error when the 422 detail is a plain string, not a Finding", async () => {
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json({ detail: "control token mismatch" }, { status: 422 }),
      ),
    );
    const call = api.setEngineDependencySweepEnabled("acme/widget", true);
    await expect(call).rejects.not.toBeInstanceOf(DependencySweepBlockedError);
    await expect(call).rejects.toThrow("422: control token mismatch");
  });

  it("falls back to a generic error when the 422 detail object is missing a required Finding field", async () => {
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json(
          { detail: { check: "dependabot_config_present", status: "fail", message: "nope" } },
          { status: 422 },
        ),
      ),
    );
    const call = api.setEngineDependencySweepEnabled("acme/widget", true);
    await expect(call).rejects.not.toBeInstanceOf(DependencySweepBlockedError);
  });

  it("never treats a non-422 error status as a Finding, even if its body matches the shape", async () => {
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json(
          { detail: { check: "c", status: "fail", message: "m", remedy: "r" } },
          { status: 403 },
        ),
      ),
    );
    const call = api.setEngineDependencySweepEnabled("acme/widget", true);
    await expect(call).rejects.not.toBeInstanceOf(DependencySweepBlockedError);
    await expect(call).rejects.toThrow(/^403: /);
  });

  it("falls back to status text when a non-422 error body is empty", async () => {
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.text("", { status: 503, statusText: "Service Unavailable" }),
      ),
    );
    await expect(
      api.setEngineDependencySweepEnabled("acme/widget", true),
    ).rejects.toThrow("503: Service Unavailable");
  });

  it("omits the control-token header when no token is stored, and attaches it when present", async () => {
    localStorage.clear();
    let captured: string | null = "not-checked";
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", ({ request }) => {
        captured = request.headers.get(CONTROL_TOKEN_HEADER);
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" } });
      }),
    );
    await api.setEngineDependencySweepEnabled("acme/widget", true);
    expect(captured).toBeNull();

    writeControlToken("my-secret-token");
    await api.setEngineDependencySweepEnabled("acme/widget", true);
    expect(captured).toBe("my-secret-token");
  });
});

const MINIMAL_ENGINE_CONFIG = makeEngineConfig();

describe("engine ops (config proposal)", () => {
  it("api.proposeEngineConfig posts {base_preset} and returns the proposal result", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/engines/acme/widget/config-proposal", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          slug: "acme/widget",
          base_preset: "python",
          detected_stacks: ["python"],
          additional_stacks: [],
          proposed_config: MINIMAL_ENGINE_CONFIG,
          diff: [],
          validation: { check: "flow_graph_and_posture_valid", status: "pass", message: "ok", remedy: "", remedy_url: "" },
        });
      }),
    );
    const result = await api.proposeEngineConfig("acme/widget", "python");
    expect(captured).toEqual({ base_preset: "python" });
    expect(result.base_preset).toBe("python");
    expect(result.proposed_config).toEqual(MINIMAL_ENGINE_CONFIG);
  });

  it("api.proposeEngineConfig defaults base_preset to null when omitted", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/engines/acme/widget/config-proposal", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          slug: "acme/widget",
          base_preset: "python",
          detected_stacks: [],
          additional_stacks: [],
          proposed_config: MINIMAL_ENGINE_CONFIG,
          diff: [],
          validation: { check: "flow_graph_and_posture_valid", status: "pass", message: "ok", remedy: "", remedy_url: "" },
        });
      }),
    );
    await api.proposeEngineConfig("acme/widget");
    expect(captured).toEqual({ base_preset: null });
  });

  it("throws ConfigProposalBlockedError carrying the Finding when refused with 422", async () => {
    server.use(
      http.post("/api/engines/acme/widget/config-proposal", () =>
        HttpResponse.json(
          {
            detail: {
              check: "config_proposal",
              status: "fail",
              message: "no forge configured",
              remedy: "seed the engine configuration from a stack preset manually",
            },
          },
          { status: 422 },
        ),
      ),
    );
    let caught: ConfigProposalBlockedError | null = null;
    try {
      await api.proposeEngineConfig("acme/widget");
    } catch (err) {
      if (err instanceof ConfigProposalBlockedError) caught = err;
    }
    expect(caught?.finding.check).toBe("config_proposal");
    expect(caught?.finding.message).toBe("no forge configured");
    expect(caught?.finding.remedy).toContain("manually");
  });

  it("falls back to a generic error when the 422 detail is a plain string, not a Finding", async () => {
    server.use(
      http.post("/api/engines/acme/widget/config-proposal", () =>
        HttpResponse.json({ detail: "bad request" }, { status: 422 }),
      ),
    );
    const call = api.proposeEngineConfig("acme/widget");
    await expect(call).rejects.not.toBeInstanceOf(ConfigProposalBlockedError);
    await expect(call).rejects.toThrow("422: bad request");
  });

  it("never treats a non-422 error status as a Finding, even if its body matches the shape", async () => {
    server.use(
      http.post("/api/engines/acme/widget/config-proposal", () =>
        HttpResponse.json(
          { detail: { check: "c", status: "fail", message: "m", remedy: "r" } },
          { status: 403 },
        ),
      ),
    );
    const call = api.proposeEngineConfig("acme/widget");
    await expect(call).rejects.not.toBeInstanceOf(ConfigProposalBlockedError);
    await expect(call).rejects.toThrow(/^403: /);
  });

  it("falls back to status text when a non-422 error body is empty", async () => {
    server.use(
      http.post("/api/engines/acme/widget/config-proposal", () =>
        HttpResponse.text("", { status: 503, statusText: "Service Unavailable" }),
      ),
    );
    await expect(api.proposeEngineConfig("acme/widget")).rejects.toThrow(
      "503: Service Unavailable",
    );
  });

  it("omits the control-token header when no token is stored, and attaches it when present", async () => {
    localStorage.clear();
    let captured: string | null = "not-checked";
    server.use(
      http.post("/api/engines/acme/widget/config-proposal", ({ request }) => {
        captured = request.headers.get(CONTROL_TOKEN_HEADER);
        return HttpResponse.json({
          slug: "acme/widget",
          base_preset: "python",
          detected_stacks: [],
          additional_stacks: [],
          proposed_config: MINIMAL_ENGINE_CONFIG,
          diff: [],
          validation: { check: "flow_graph_and_posture_valid", status: "pass", message: "ok", remedy: "", remedy_url: "" },
        });
      }),
    );
    await api.proposeEngineConfig("acme/widget");
    expect(captured).toBeNull();

    writeControlToken("my-secret-token");
    await api.proposeEngineConfig("acme/widget");
    expect(captured).toBe("my-secret-token");
  });

  it("api.validateEngineConfigDraft posts the draft config and returns the validation Finding", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/engines/acme/widget/config-draft-validate", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          check: "flow_graph_and_posture_valid",
          status: "fail",
          message: "merge team 'pr_merge' has no predecessors",
          remedy: "",
          remedy_url: "",
        });
      }),
    );
    const finding = await api.validateEngineConfigDraft("acme/widget", MINIMAL_ENGINE_CONFIG);
    expect(captured).toEqual(MINIMAL_ENGINE_CONFIG);
    expect(finding.status).toBe("fail");
    expect(finding.message).toContain("pr_merge");
  });
});

describe("engine ops (repo readiness onboarding)", () => {
  it("api.scaffoldEngine posts to the scaffold sub-resource and returns the onboarding state", async () => {
    let called = false;
    server.use(
      http.post("/api/engines/acme/widget/scaffold", () => {
        called = true;
        return HttpResponse.json({
          slug: "acme/widget",
          stack: "python",
          scaffold_pr_number: 7,
          scaffold_pr_url: "https://github.com/acme/widget/pull/7",
          authoritative_checks: ["python-ci"],
          protection_applied_keys: [],
          protection_deferred: [],
        });
      }),
    );
    const state = await api.scaffoldEngine("acme/widget");
    expect(called).toBe(true);
    expect(state.scaffold_pr_number).toBe(7);
    expect(state.scaffold_pr_url).toBe("https://github.com/acme/widget/pull/7");
  });

  it("api.scaffoldEngine surfaces the 422 detail string on failure", async () => {
    server.use(
      http.post("/api/engines/acme/widget/scaffold", () =>
        HttpResponse.json({ detail: "no transport credential for 'acme/widget'" }, { status: 422 }),
      ),
    );
    await expect(api.scaffoldEngine("acme/widget")).rejects.toThrow(
      "422: no transport credential",
    );
  });

  it("api.protectEngine posts to the protect sub-resource and returns applied/deferred rows", async () => {
    let called = false;
    server.use(
      http.post("/api/engines/acme/widget/protect", () => {
        called = true;
        return HttpResponse.json({
          slug: "acme/widget",
          stack: "python",
          scaffold_pr_number: 7,
          scaffold_pr_url: "https://github.com/acme/widget/pull/7",
          authoritative_checks: ["python-ci"],
          protection_applied_keys: ["squash_merge_enabled", "delete_branch_on_merge"],
          protection_deferred: [
            {
              check: "required_checks",
              status: "unknown",
              message: "no credential available to apply branch protection",
              remedy: "Add each of the engine's authoritative checks",
              remedy_url: "https://github.com/acme/widget/settings/branches",
            },
          ],
        });
      }),
    );
    const state = await api.protectEngine("acme/widget");
    expect(called).toBe(true);
    expect(state.protection_applied_keys).toEqual([
      "squash_merge_enabled",
      "delete_branch_on_merge",
    ]);
    expect(state.protection_deferred).toHaveLength(1);
    expect(state.protection_deferred[0]?.remedy_url).toBe(
      "https://github.com/acme/widget/settings/branches",
    );
  });

  it("api.getEngineOnboarding fetches the readiness checklist and overall verdict", async () => {
    server.use(
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json({
          state: {
            slug: "acme/widget",
            stack: null,
            scaffold_pr_number: null,
            scaffold_pr_url: null,
            authoritative_checks: [],
            protection_applied_keys: [],
            protection_deferred: [],
          },
          checklist: [
            {
              check: "push_repository",
              status: "fail",
              message: "repository has no default-branch HEAD yet",
              remedy: "push the existing repository's initial commit(s) to GitHub",
            },
          ],
          overall: "fail",
        }),
      ),
    );
    const readiness = await api.getEngineOnboarding("acme/widget");
    expect(readiness.overall).toBe("fail");
    expect(readiness.checklist).toHaveLength(1);
    expect(readiness.checklist[0]?.check).toBe("push_repository");
    expect(readiness.state.scaffold_pr_number).toBeNull();
  });

  it("api.getEngineOnboarding surfaces the 422 detail when the engine has no forge configured", async () => {
    server.use(
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json({ detail: "engine 'acme/widget' has no forge configured" }, { status: 422 }),
      ),
    );
    await expect(api.getEngineOnboarding("acme/widget")).rejects.toThrow(
      "422: engine 'acme/widget' has no forge configured",
    );
  });
});

describe("classifyEngineConfigFinding", () => {
  it.each([
    "merge team 'pr_merge' has no predecessors in the graph",
    "merge team 'pr_merge' reachable from non-verifier teams: {'implementation'}",
    "team 'opportunity_scout' cannot reach any terminal state",
    "internal-only team 'decomposer' has unauthorized predecessors: {'research'}",
    "cycle with no terminal or exit edge: frozenset({'research', 'brainstorming'})",
    "merge team 'pr_merge' admitted at posture 'self_merge' but verifier 'verification' requires 'self_merge'",
  ])("scopes a flow-graph/posture validator message %s as roster", (message) => {
    expect(classifyEngineConfigFinding(message)).toEqual({ scope: "roster", message });
  });

  it.each([
    "no secret record for 'SPROCKET_GH_TOKEN'",
    "repo in body must match owner/repo in URL",
    "engine not found",
  ])("scopes an unrelated error message %s as general", (message) => {
    expect(classifyEngineConfigFinding(message)).toEqual({ scope: "general", message });
  });
});

describe("api error parsing", () => {
  it("throws with parsed detail on JSON error body", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ detail: "bad" }, { status: 502 }),
      ),
    );
    await expect(api.createWorkspace("t", "")).rejects.toThrow("502: bad");
  });

  it("throws with raw body when error body is not JSON", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.text("plain text error", { status: 500 }),
      ),
    );
    await expect(api.createWorkspace("t", "")).rejects.toThrow("500: plain text error");
  });

  it("throws with empty body falls back to status text", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.text("", { status: 503, statusText: "Service Unavailable" }),
      ),
    );
    await expect(api.createWorkspace("t", "")).rejects.toThrow("503: Service Unavailable");
  });

  it("throws with raw body when JSON parses but detail is not a string", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ detail: { nested: "object" } }, { status: 422 }),
      ),
    );
    await expect(api.createWorkspace("t", "")).rejects.toThrow(/^422: /);
  });

  it("throws with structured detail (code: reason) format when both code and reason present", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json(
          { detail: { detail: "proposal_parse_failed", reason: "scope_audit issue" } },
          { status: 422 },
        ),
      ),
    );
    await expect(api.createWorkspace("t", "")).rejects.toThrow(
      "422: proposal_parse_failed: scope_audit issue",
    );
  });

  it("sends JSON content type", async () => {
    server.use(
      http.post("/api/workspaces", ({ request }) => {
        expect(request.headers.get("Content-Type")).toBe("application/json");
        return HttpResponse.json({ task_id: "x", root_task_ids: ["x"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("t", "d");
  });
});

describe("api.ApiError (workspace action wrappers)", () => {
  it.each([
    ["archiveWorkspace", "archive", () => api.archiveWorkspace("ws-1")],
    ["unarchiveWorkspace", "unarchive", () => api.unarchiveWorkspace("ws-1")],
    ["stopWorkspace", "stop", () => api.stopWorkspace("ws-1")],
    ["resumeWorkspace", "resume", () => api.resumeWorkspace("ws-1")],
  ] as const)(
    "%s rejects with an ApiError carrying .status and an unchanged .message",
    async (_name, suffix, call) => {
      server.use(
        http.post(`/api/workspaces/ws-1/${suffix}`, () =>
          HttpResponse.json({ detail: "not eligible" }, { status: 409 }),
        ),
      );
      let caught: ApiError | null = null;
      try {
        await call();
      } catch (err) {
        if (err instanceof ApiError) caught = err;
      }
      expect(caught).not.toBeNull();
      expect(caught?.status).toBe(409);
      expect(caught?.message).toBe("409: not eligible");
    },
  );

  it("a non-action workspace caller (createWorkspace) still formats its error message identically", async () => {
    server.use(
      http.post("/api/workspaces", () => HttpResponse.json({ detail: "bad" }, { status: 422 })),
    );
    let caught: ApiError | null = null;
    try {
      await api.createWorkspace("t", "");
    } catch (err) {
      if (err instanceof ApiError) caught = err;
    }
    expect(caught?.status).toBe(422);
    expect(caught?.message).toBe("422: bad");
  });
});

describe("control-token header attach through the generated client", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("omits the header when no token is stored", async () => {
    let captured: string | null = "not-checked";
    server.use(
      http.post("/api/workspaces", ({ request }) => {
        captured = request.headers.get(CONTROL_TOKEN_HEADER);
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("T", "D");
    expect(captured).toBeNull();
  });

  it("attaches the exact stored token when present", async () => {
    writeControlToken("my-secret-token");
    let captured: string | null = null;
    server.use(
      http.post("/api/workspaces", ({ request }) => {
        captured = request.headers.get(CONTROL_TOKEN_HEADER);
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("T", "D");
    expect(captured).toBe("my-secret-token");
  });

  it("still sends the Content-Type header unchanged when a token is present", async () => {
    writeControlToken("my-secret-token");
    server.use(
      http.post("/api/workspaces", ({ request }) => {
        expect(request.headers.get("Content-Type")).toBe("application/json");
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("T", "D");
  });
});

describe("parseSseChunk", () => {
  it("parses a complete frame", () => {
    const { events, remainder } = parseSseChunk('event: foo\ndata: {"a":1}\n\n');
    expect(events).toEqual([{ event: "foo", data: '{"a":1}' }]);
    expect(remainder).toBe("");
  });

  it("keeps partial frame in remainder", () => {
    const { events, remainder } = parseSseChunk("event: foo\ndata: x");
    expect(events).toEqual([]);
    expect(remainder).toBe("event: foo\ndata: x");
  });

  it("parses multiple frames in one buffer", () => {
    const { events } = parseSseChunk("event: a\ndata: 1\n\nevent: b\ndata: 2\n\n");
    expect(events).toEqual([
      { event: "a", data: "1" },
      { event: "b", data: "2" },
    ]);
  });

  it("skips empty frames from extra blank lines", () => {
    const { events } = parseSseChunk("\n\nevent: x\ndata: y\n\n");
    expect(events).toEqual([{ event: "x", data: "y" }]);
  });

  it("defaults event name to 'message' when not specified", () => {
    const { events } = parseSseChunk("data: hello\n\n");
    expect(events).toEqual([{ event: "message", data: "hello" }]);
  });

  it("ignores unknown field lines", () => {
    const { events } = parseSseChunk("event: x\ndata: y\nid: 123\n\n");
    expect(events).toEqual([{ event: "x", data: "y" }]);
  });
});

describe("api.createWorkspace", () => {
  it("posts title + description and returns task_id", async () => {
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        const body = (await request.json()) as {
          title: string;
          description: string;
        };
        expect(body).toEqual({ title: "T", description: "D" });
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    const res = await api.createWorkspace("T", "D");
    expect(res).toEqual({ task_id: "t", root_task_ids: ["t"] });
  });

  it("includes tasks and dependencies when tasks is non-empty", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "ws-1", root_task_ids: ["t1"] }, { status: 201 });
      }),
    );
    const tasks: TaskProposal[] = [
      { id: "t1", title: "T1", description: "d", team: "implementation_plan", instructions: null, scope_audit: [] },
    ];
    await api.createWorkspace("T", "D", tasks, []);
    expect(captured).toEqual({ title: "T", description: "D", tasks, dependencies: [] });
  });

  it("omits tasks and dependencies when tasks is empty array", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("T", "D", [], []);
    expect(captured).toEqual({ title: "T", description: "D" });
  });

  it("omits tasks and dependencies when tasks is undefined", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("T", "D");
    expect((captured as Record<string, unknown>).tasks).toBeUndefined();
  });

  it("uses empty array for dependencies when tasks provided but dependencies omitted", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    const tasks: TaskProposal[] = [
      { id: "t1", title: "T1", description: "d", team: "implementation_plan", instructions: null, scope_audit: [] },
    ];
    await api.createWorkspace("T", "D", tasks, undefined);
    expect((captured as Record<string, unknown>).dependencies).toEqual([]);
  });

  it("propagates error responses", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ detail: "bad" }, { status: 422 }),
      ),
    );
    await expect(api.createWorkspace("T", "D")).rejects.toThrow("422: bad");
  });

  it("includes requirements when provided", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    const reqs = [
      {
        id: "req-x",
        text: "must pass",
        test_ref: "test_auth",
        status: "PENDING" as const,
        merged_pr_url: null,
        withdrawn_by_question_id: null,
      },
    ];
    await api.createWorkspace("T", "D", undefined, undefined, reqs);
    expect((captured as Record<string, unknown>).requirements).toEqual(reqs);
  });

  it("omits requirements when null", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("T", "D", undefined, undefined, null);
    expect((captured as Record<string, unknown>).requirements).toBeUndefined();
  });

  it("mints requirements from acceptanceClauses", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("T", "D", undefined, undefined, undefined, [
      "clause one",
      "   ",
      "  clause two  ",
    ]);
    expect((captured as Record<string, unknown>).requirements).toEqual([
      { text: "clause one" },
      { text: "clause two" },
    ]);
  });

  it("omits requirements when acceptanceClauses is empty", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 });
      }),
    );
    await api.createWorkspace("T", "D", undefined, undefined, undefined, []);
    expect((captured as Record<string, unknown>).requirements).toBeUndefined();
  });
});

describe("api.dispatchTask", () => {
  it("posts to per-task dispatch route with team only", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces/ws-1/tasks/t-1/dispatch", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ job_id: 2, invocation_id: "inv_001" }, { status: 202 });
      }),
    );
    const res = await api.dispatchTask("ws-1", "t-1", "implementation_plan");
    expect(captured).toEqual({ team: "implementation_plan" });
    expect(res.job_id).toBe(2);
  });

  it("omits blank instructions", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces/ws-1/tasks/t-1/dispatch", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ job_id: 4, invocation_id: "inv_003" }, { status: 202 });
      }),
    );
    await api.dispatchTask("ws-1", "t-1", "research", { instructions: "  " });
    expect(captured).toEqual({ team: "research" });
  });

  it("includes non-blank instructions", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces/ws-1/tasks/t-1/dispatch", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ job_id: 5, invocation_id: "inv_004" }, { status: 202 });
      }),
    );
    await api.dispatchTask("ws-1", "t-1", "research", { instructions: "Focus here." });
    expect(captured).toEqual({ team: "research", instructions: "Focus here." });
  });

  it("rejects invalid workspaceId", async () => {
    await expect(api.dispatchTask("BAD ID", "t-1", "research")).rejects.toThrow(/invalid task_id/);
  });

  it("includes counter_case in the dispatch body when provided, so the backend can log the waiver", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces/ws-1/tasks/t-1/dispatch", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ job_id: 6, invocation_id: "inv_006" }, { status: 202 });
      }),
    );
    const counterCase: CounterCase = {
      against: "scope is still shifting",
      cheapest_80: "ship the smaller half first",
      cost_estimate: "insufficient data",
    };
    await api.dispatchTask("ws-1", "t-1", "research", { counter_case: counterCase });
    expect(captured).toEqual({ team: "research", counter_case: counterCase });
  });

  it("omits counter_case from the dispatch body when not provided", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces/ws-1/tasks/t-1/dispatch", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ job_id: 7, invocation_id: "inv_007" }, { status: 202 });
      }),
    );
    await api.dispatchTask("ws-1", "t-1", "research");
    expect(captured).toEqual({ team: "research" });
  });

  it("rejects invalid taskId", async () => {
    await expect(api.dispatchTask("ws-1", "BAD ID", "research")).rejects.toThrow(/invalid task_id/);
  });

  it("propagates backend errors", async () => {
    server.use(
      http.post("/api/workspaces/ws-1/tasks/t-1/dispatch", () =>
        HttpResponse.json({ detail: "task not found" }, { status: 404 }),
      ),
    );
    await expect(api.dispatchTask("ws-1", "t-1", "research")).rejects.toThrow("404: task not found");
  });
});

describe("api.createAndDispatchWorkspace", () => {
  it("posts title, description, and team in a single call and returns task_id + root_task_ids", async () => {
    let captured: unknown = null;
    let calls = 0;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        calls += 1;
        captured = await request.json();
        return HttpResponse.json({ task_id: "ws-1", root_task_ids: ["ws-1"] }, { status: 201 });
      }),
    );
    const res = await api.createAndDispatchWorkspace("T", "D", "research");
    expect(calls).toBe(1);
    expect(captured).toEqual({ team: "research", title: "T", description: "D" });
    expect(res).toEqual({ task_id: "ws-1", root_task_ids: ["ws-1"] });
  });

  it("includes non-blank instructions and omits blank instructions", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "ws-2", root_task_ids: ["ws-2"] }, { status: 201 });
      }),
    );
    await api.createAndDispatchWorkspace("T", "D", "research", { instructions: "Focus here." });
    expect(captured).toEqual({
      team: "research",
      title: "T",
      description: "D",
      instructions: "Focus here.",
    });

    await api.createAndDispatchWorkspace("T", "D", "research", { instructions: "   " });
    expect(captured).toEqual({ team: "research", title: "T", description: "D" });
  });

  it("includes counter_case when provided and omits it when not", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "ws-3", root_task_ids: ["ws-3"] }, { status: 201 });
      }),
    );
    const counterCase: CounterCase = {
      against: "scope is still shifting",
      cheapest_80: "ship the smaller half first",
      cost_estimate: "insufficient data",
    };
    await api.createAndDispatchWorkspace("T", "D", "research", { counter_case: counterCase });
    expect(captured).toEqual({
      team: "research",
      title: "T",
      description: "D",
      counter_case: counterCase,
    });

    await api.createAndDispatchWorkspace("T", "D", "research");
    expect(captured).toEqual({ team: "research", title: "T", description: "D" });
  });

  it("derives requirements from non-blank acceptanceClauses and omits them when empty", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "ws-4", root_task_ids: ["ws-4"] }, { status: 201 });
      }),
    );
    await api.createAndDispatchWorkspace("T", "D", "research", {}, {
      acceptanceClauses: ["clause one", "   ", "  clause two  "],
    });
    expect((captured as Record<string, unknown>).requirements).toEqual([
      { text: "clause one" },
      { text: "clause two" },
    ]);

    await api.createAndDispatchWorkspace("T", "D", "research", {}, { acceptanceClauses: [] });
    expect((captured as Record<string, unknown>).requirements).toBeUndefined();
  });

  it("propagates backend errors", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ detail: "unknown team: bogus" }, { status: 400 }),
      ),
    );
    await expect(api.createAndDispatchWorkspace("T", "D", "bogus")).rejects.toThrow(
      "400: unknown team: bogus",
    );
  });

  it("includes project_id when provided and omits it when undefined or null", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "ws-5", root_task_ids: ["ws-5"] }, { status: 201 });
      }),
    );
    await api.createAndDispatchWorkspace("T", "D", "research", {}, { projectId: "proj-1" });
    expect(captured).toEqual({
      team: "research",
      title: "T",
      description: "D",
      project_id: "proj-1",
    });

    await api.createAndDispatchWorkspace("T", "D", "research", {}, { projectId: null });
    expect(captured).toEqual({ team: "research", title: "T", description: "D" });

    await api.createAndDispatchWorkspace("T", "D", "research");
    expect(captured).toEqual({ team: "research", title: "T", description: "D" });
  });

  it("includes roadmap_item_id when non-blank and omits it when undefined, null, or blank", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "ws-6", root_task_ids: ["ws-6"] }, { status: 201 });
      }),
    );
    await api.createAndDispatchWorkspace("T", "D", "research", {}, { roadmapItemId: "itm-1" });
    expect(captured).toEqual({
      team: "research",
      title: "T",
      description: "D",
      roadmap_item_id: "itm-1",
    });

    await api.createAndDispatchWorkspace("T", "D", "research", {}, { roadmapItemId: null });
    expect(captured).toEqual({ team: "research", title: "T", description: "D" });

    await api.createAndDispatchWorkspace("T", "D", "research", {}, { roadmapItemId: "   " });
    expect(captured).toEqual({ team: "research", title: "T", description: "D" });

    await api.createAndDispatchWorkspace("T", "D", "research");
    expect(captured).toEqual({ team: "research", title: "T", description: "D" });
  });

  it("includes engine_key when the caller passes one and omits it when undefined or null", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ task_id: "ws-7", root_task_ids: ["ws-7"] }, { status: 201 });
      }),
    );
    await api.createAndDispatchWorkspace("T", "D", "maintenance", {}, { engineKey: "acme/widget" });
    expect(captured).toEqual({
      team: "maintenance",
      title: "T",
      description: "D",
      engine_key: "acme/widget",
    });

    await api.createAndDispatchWorkspace("T", "D", "maintenance", {}, { engineKey: null });
    expect(captured).toEqual({ team: "maintenance", title: "T", description: "D" });

    await api.createAndDispatchWorkspace("T", "D", "maintenance");
    expect(captured).toEqual({ team: "maintenance", title: "T", description: "D" });
  });
});

describe("api.listWorkspaces", () => {
  it("returns the workspaces array", async () => {
    server.use(
      http.get("/api/workspaces", () =>
        HttpResponse.json([
          {
            task_id: "a",
            title: "A",
            status: "active",
            created_at: "2026-05-15T12:00:00Z",
            updated_at: "2026-05-15T12:05:00Z",
          },
        ]),
      ),
    );
    const res = await api.listWorkspaces();
    expect(res).toHaveLength(1);
    expect(res[0]?.task_id).toBe("a");
    expect(res[0]?.created_at).toBe("2026-05-15T12:00:00Z");
  });

  it("calls the bare /api/workspaces URL with no query params", async () => {
    let capturedInclude: string | null = "not-checked";
    server.use(
      http.get("/api/workspaces", ({ request }) => {
        capturedInclude = new URL(request.url).searchParams.get("include");
        return HttpResponse.json([]);
      }),
    );
    await api.listWorkspaces();
    expect(capturedInclude).toBeNull();
  });
});

describe("api.getWorkspace", () => {
  it("returns the detail payload", async () => {
    server.use(
      http.get("/api/workspaces/abc", () =>
        HttpResponse.json({
          task_id: "abc",
          title: "T",
          description: "D",
          status: "active",
          revisit_counts: {},
          created_at: "c",
          updated_at: "u",
          history: [],
          latest_handover: null,
        }),
      ),
    );
    const res = await api.getWorkspace("abc");
    expect(res.task_id).toBe("abc");
    expect(res.latest_handover).toBeNull();
  });

  it("rejects task ids that would yield an unsafe URL", async () => {
    await expect(api.getWorkspace("a/b")).rejects.toThrow(
      /unsafe request path/,
    );
  });

  it("calls bare URL without include param when no opts given", async () => {
    let capturedInclude: string | null = "not-checked";
    server.use(
      http.get("/api/workspaces/abc", ({ request }) => {
        capturedInclude = new URL(request.url).searchParams.get("include");
        return HttpResponse.json({
          task_id: "abc", title: "T", description: "D", status: "active",
          revisit_counts: {},
          created_at: "c", updated_at: "u", history: [],
          latest_handover: null,
        });
      }),
    );
    await api.getWorkspace("abc");
    expect(capturedInclude).toBeNull();
  });

  it("appends ?include=triage when includeTriage is true", async () => {
    let capturedInclude: string | null = null;
    server.use(
      http.get("/api/workspaces/abc", ({ request }) => {
        capturedInclude = new URL(request.url).searchParams.get("include");
        return HttpResponse.json({
          task_id: "abc", title: "T", description: "D", status: "active",
          revisit_counts: {},
          created_at: "c", updated_at: "u", history: [],
          latest_handover: null,
        });
      }),
    );
    await api.getWorkspace("abc", { includeTriage: true });
    expect(capturedInclude).toBe("triage");
  });
});

describe("api build/explore chat dispatch", () => {
  it("listBuildChatSessions omits project_id when not provided", async () => {
    let captured: string | null = "not-checked";
    server.use(
      http.get("/api/build-chat/sessions", ({ request }) => {
        captured = new URL(request.url).searchParams.get("project_id");
        return HttpResponse.json([]);
      }),
    );
    await api.listBuildChatSessions();
    expect(captured).toBeNull();
  });

  it("listBuildChatSessions passes project_id as a query param", async () => {
    let captured: string | null = null;
    server.use(
      http.get("/api/build-chat/sessions", ({ request }) => {
        captured = new URL(request.url).searchParams.get("project_id");
        return HttpResponse.json([]);
      }),
    );
    await api.listBuildChatSessions("proj-a");
    expect(captured).toBe("proj-a");
  });

  it("listExploreChatSessions omits project_id when not provided", async () => {
    let captured: string | null = "not-checked";
    server.use(
      http.get("/api/explore-chat/sessions", ({ request }) => {
        captured = new URL(request.url).searchParams.get("project_id");
        return HttpResponse.json([]);
      }),
    );
    await api.listExploreChatSessions();
    expect(captured).toBeNull();
  });

  it("listExploreChatSessions passes project_id as a query param", async () => {
    let captured: string | null = null;
    server.use(
      http.get("/api/explore-chat/sessions", ({ request }) => {
        captured = new URL(request.url).searchParams.get("project_id");
        return HttpResponse.json([]);
      }),
    );
    await api.listExploreChatSessions("proj-b");
    expect(captured).toBe("proj-b");
  });

  it("listBuildChatSessions omits engine_key when not provided", async () => {
    let captured: string | null = "not-checked";
    server.use(
      http.get("/api/build-chat/sessions", ({ request }) => {
        captured = new URL(request.url).searchParams.get("engine_key");
        return HttpResponse.json([]);
      }),
    );
    await api.listBuildChatSessions();
    expect(captured).toBeNull();
  });

  it("listBuildChatSessions passes engine_key as a query param alongside project_id", async () => {
    let capturedEngine: string | null = null;
    let capturedProject: string | null = null;
    server.use(
      http.get("/api/build-chat/sessions", ({ request }) => {
        const params = new URL(request.url).searchParams;
        capturedEngine = params.get("engine_key");
        capturedProject = params.get("project_id");
        return HttpResponse.json([]);
      }),
    );
    await api.listBuildChatSessions("proj-a", "acme/widget");
    expect(capturedEngine).toBe("acme/widget");
    expect(capturedProject).toBe("proj-a");
  });

  it("listExploreChatSessions omits engine_key when not provided", async () => {
    let captured: string | null = "not-checked";
    server.use(
      http.get("/api/explore-chat/sessions", ({ request }) => {
        captured = new URL(request.url).searchParams.get("engine_key");
        return HttpResponse.json([]);
      }),
    );
    await api.listExploreChatSessions();
    expect(captured).toBeNull();
  });

  it("listExploreChatSessions passes engine_key as a query param", async () => {
    let captured: string | null = null;
    server.use(
      http.get("/api/explore-chat/sessions", ({ request }) => {
        captured = new URL(request.url).searchParams.get("engine_key");
        return HttpResponse.json([]);
      }),
    );
    await api.listExploreChatSessions(undefined, "acme/widget");
    expect(captured).toBe("acme/widget");
  });

});

describe("api.deleteWorkspace", () => {
  it("resolves with no value on 204", async () => {
    server.use(
      http.delete("/api/workspaces/abc", () => new HttpResponse(null, { status: 204 })),
    );
    await expect(api.deleteWorkspace("abc")).resolves.toBeUndefined();
  });

  it("throws with parsed detail on 404", async () => {
    server.use(
      http.delete("/api/workspaces/ghost", () =>
        HttpResponse.json({ detail: "workspace not found: ghost" }, { status: 404 }),
      ),
    );
    await expect(api.deleteWorkspace("ghost")).rejects.toThrow(
      "404: workspace not found: ghost",
    );
  });

  it("throws with parsed detail on 409", async () => {
    server.use(
      http.delete("/api/workspaces/busy", () =>
        HttpResponse.json({ detail: "workspace has active jobs" }, { status: 409 }),
      ),
    );
    await expect(api.deleteWorkspace("busy")).rejects.toThrow(
      "409: workspace has active jobs",
    );
  });

  it("rejects invalid task ids without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.deleteWorkspace("BAD ID")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.archiveWorkspace", () => {
  it("resolves with no value on 204", async () => {
    server.use(
      http.post(
        "/api/workspaces/abc/archive",
        () => new HttpResponse(null, { status: 204 }),
      ),
    );
    await expect(api.archiveWorkspace("abc")).resolves.toBeUndefined();
  });

  it("throws with parsed detail on 404", async () => {
    server.use(
      http.post("/api/workspaces/ghost/archive", () =>
        HttpResponse.json({ detail: "workspace not found: ghost" }, { status: 404 }),
      ),
    );
    await expect(api.archiveWorkspace("ghost")).rejects.toThrow(
      "404: workspace not found: ghost",
    );
  });

  it("throws with parsed detail on 409", async () => {
    server.use(
      http.post("/api/workspaces/busy/archive", () =>
        HttpResponse.json({ detail: "workspace has active jobs" }, { status: 409 }),
      ),
    );
    await expect(api.archiveWorkspace("busy")).rejects.toThrow(
      "409: workspace has active jobs",
    );
  });

  it("rejects invalid task ids without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.archiveWorkspace("BAD ID")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.unarchiveWorkspace", () => {
  it("resolves with no value on 204", async () => {
    server.use(
      http.post(
        "/api/workspaces/abc/unarchive",
        () => new HttpResponse(null, { status: 204 }),
      ),
    );
    await expect(api.unarchiveWorkspace("abc")).resolves.toBeUndefined();
  });

  it("throws with parsed detail on 404", async () => {
    server.use(
      http.post("/api/workspaces/ghost/unarchive", () =>
        HttpResponse.json({ detail: "workspace not found: ghost" }, { status: 404 }),
      ),
    );
    await expect(api.unarchiveWorkspace("ghost")).rejects.toThrow(
      "404: workspace not found: ghost",
    );
  });

  it("throws with parsed detail on 409", async () => {
    server.use(
      http.post("/api/workspaces/active/unarchive", () =>
        HttpResponse.json({ detail: "workspace is not archived" }, { status: 409 }),
      ),
    );
    await expect(api.unarchiveWorkspace("active")).rejects.toThrow(
      "409: workspace is not archived",
    );
  });

  it("rejects invalid task ids without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.unarchiveWorkspace("BAD ID")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.subscribeWorkspace", () => {
  it("delivers parsed events to onEvent", async () => {
    const body =
      'event: a\ndata: {"k":"v"}\n\nevent: b\ndata: {"k":"w"}\n\n';
    server.use(
      http.get("/api/workspaces/t/events", () =>
        new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } }),
      ),
    );
    const received: ParsedSseEvent[] = [];
    let errored: string | null = null;
    const unsubscribe = api.subscribeWorkspace("t", {
      onEvent: (e) => received.push(e),
      onError: (d) => {
        errored = d;
      },
    });
    await vi.waitFor(() => {
      expect(received.length).toBe(2);
    });
    unsubscribe();
    expect(errored).toBeNull();
    expect(received[0]?.event).toBe("a");
  });

  it("reports stream-open failures with status and parsed detail", async () => {
    server.use(
      http.get("/api/workspaces/t/events", () =>
        HttpResponse.json({ detail: "workspace not found" }, { status: 404 }),
      ),
    );
    const errors: string[] = [];
    api.subscribeWorkspace("t", {
      onEvent: () => undefined,
      onError: (d) => errors.push(d),
    });
    await vi.waitFor(() => {
      expect(errors.length).toBe(1);
    });
    expect(errors[0]).toContain("404");
    expect(errors[0]).toContain("workspace not found");
  });

  it("reports response-body-missing as a distinct error", async () => {
    const originalFetch = globalThis.fetch;
    const fakeFetch: typeof fetch = () =>
      Promise.resolve(new Response(null, { status: 200 }));
    globalThis.fetch = fakeFetch;
    try {
      const errors: string[] = [];
      api.subscribeWorkspace("t", {
        onEvent: () => undefined,
        onError: (d) => errors.push(d),
      });
      await vi.waitFor(() => {
        expect(errors).toEqual(["response body missing"]);
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("reports network failure via onError", async () => {
    server.use(
      http.get("/api/workspaces/t/events", () => HttpResponse.error()),
    );
    const errors: string[] = [];
    api.subscribeWorkspace("t", {
      onEvent: () => undefined,
      onError: (d) => errors.push(d),
    });
    await vi.waitFor(() => {
      expect(errors.length).toBe(1);
    });
  });

  it("reports non-Error throws via onError", async () => {
    const originalFetch = globalThis.fetch;
    const fakeFetch = (): Promise<Response> => {
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      return Promise.reject("string thrown");
    };
    globalThis.fetch = fakeFetch;
    try {
      const errors: string[] = [];
      api.subscribeWorkspace("t", {
        onEvent: () => undefined,
        onError: (d) => errors.push(d),
      });
      await vi.waitFor(() => {
        expect(errors).toEqual(["string thrown"]);
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("swallows AbortError when consumer cancels", async () => {
    const resolver: { fn: (() => void) | null } = { fn: null };
    const streamPromise = new Promise<void>((resolve) => {
      resolver.fn = resolve;
    });
    server.use(
      http.get("/api/workspaces/t/events", async () => {
        await streamPromise;
        return new HttpResponse("event: x\ndata: {}\n\n", {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );
    const errors: string[] = [];
    const unsubscribe = api.subscribeWorkspace("t", {
      onEvent: () => undefined,
      onError: (d) => errors.push(d),
    });
    unsubscribe();
    resolver.fn?.();
    await new Promise((r) => setTimeout(r, 10));
    expect(errors).toEqual([]);
  });
});

describe("api invocations", () => {
  it("listInvocations returns ids", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001", "inv_002"]),
      ),
    );
    expect(await api.listInvocations("t1")).toEqual(["inv_001", "inv_002"]);
  });

  it("listInvocations rejects an unsafe task id", async () => {
    await expect(api.listInvocations("a b")).rejects.toThrow(/invalid task_id/);
  });

  it("listInvocations rejects an empty task id", async () => {
    await expect(api.listInvocations("")).rejects.toThrow(/invalid task_id/);
  });

  it("getInvocation rejects an unsafe inv id", async () => {
    await expect(api.getInvocation("ws", "inv_1")).rejects.toThrow(
      /invalid inv_id/,
    );
  });

  it("getInvocation rejects an unsafe task id", async () => {
    await expect(api.getInvocation("../bad", "inv_001")).rejects.toThrow(
      /invalid task_id/,
    );
  });

  it("getAgentStream rejects an unsafe role", async () => {
    await expect(
      api.getAgentStream("ws", "inv_001", "../escape"),
    ).rejects.toThrow(/invalid role/);
  });

  it("getAgentStream rejects an unsafe task id", async () => {
    await expect(
      api.getAgentStream("../bad", "inv_001", "alpha"),
    ).rejects.toThrow(/invalid task_id/);
  });

  it("getAgentStream rejects an unsafe inv id", async () => {
    await expect(api.getAgentStream("ws", "bad", "alpha")).rejects.toThrow(
      /invalid inv_id/,
    );
  });

  it("getInvocation returns the WorkflowDoc", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "research",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: null,
          tasks: [],
        }),
      ),
    );
    const doc = await api.getInvocation("t1", "inv_001");
    expect(doc.id).toBe("inv_001");
    expect(doc.team).toBe("research");
  });

  it("getDispatchInstructions returns the workspace-scoped readback map", async () => {
    server.use(
      http.get("/api/workspaces/t1/dispatch-instructions", () =>
        HttpResponse.json({
          inv_001: { status: "attached", text: "do it" },
          inv_002: { status: "none", text: null },
          inv_003: { status: "not_delivered", text: "merge it" },
          inv_004: { status: "unknown", text: null },
        }),
      ),
    );
    const readbacks = await api.getDispatchInstructions("t1");
    expect(readbacks.inv_001).toEqual({ status: "attached", text: "do it" });
    expect(readbacks.inv_002).toEqual({ status: "none", text: null });
    expect(readbacks.inv_003).toEqual({ status: "not_delivered", text: "merge it" });
    expect(readbacks.inv_004).toEqual({ status: "unknown", text: null });
  });

  it("getDispatchInstructions rejects an unsafe task id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.getDispatchInstructions("../bad")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("getDispatchInstructions propagates backend errors", async () => {
    server.use(
      http.get("/api/workspaces/missing/dispatch-instructions", () =>
        HttpResponse.json({ detail: "workspace not found: missing" }, { status: 404 }),
      ),
    );
    await expect(api.getDispatchInstructions("missing")).rejects.toThrow(
      "404: workspace not found: missing",
    );
  });

  it("getStickyInstructions returns the persisted text and origin", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
    );
    const sticky = await api.getStickyInstructions("t1");
    expect(sticky).toEqual({ text: "focus here", origin: "dispatch" });
  });

  it("getStickyInstructions rejects an unsafe task id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.getStickyInstructions("../bad")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("getStickyInstructions propagates a 404 for an unknown workspace", async () => {
    server.use(
      http.get("/api/workspaces/missing/sticky-instructions", () =>
        HttpResponse.json({ detail: "workspace not found: missing" }, { status: 404 }),
      ),
    );
    await expect(api.getStickyInstructions("missing")).rejects.toThrow(
      "404: workspace not found: missing",
    );
  });

  it("clearStickyInstructions resolves on a 204 with no body", async () => {
    let called = false;
    server.use(
      http.delete("/api/workspaces/t1/sticky-instructions", () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await expect(api.clearStickyInstructions("t1")).resolves.toBeUndefined();
    expect(called).toBe(true);
  });

  it("clearStickyInstructions rejects an unsafe task id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.clearStickyInstructions("../bad")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("clearStickyInstructions propagates backend errors", async () => {
    server.use(
      http.delete("/api/workspaces/missing/sticky-instructions", () =>
        HttpResponse.json({ detail: "workspace not found: missing" }, { status: 404 }),
      ),
    );
    await expect(api.clearStickyInstructions("missing")).rejects.toThrow(
      "404: workspace not found: missing",
    );
  });

  it("getAgentStream returns the parsed event list", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/alpha/stream",
        () =>
          HttpResponse.json([
            { type: "system" },
            { type: "result", result: "ok" },
          ]),
      ),
    );
    const events = await api.getAgentStream("t1", "inv_001", "alpha");
    expect(events.map((e) => e.type)).toEqual(["system", "result"]);
  });

  it("fetchNodeResult returns the node result doc", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001/agents/worker/result", () =>
        HttpResponse.json({
          role: "worker",
          model: "sonnet",
          result_text: "done",
          completed_at: "2026-05-12T08:00:10Z",
        }),
      ),
    );
    const doc = await api.fetchNodeResult("t1", "inv_001", "worker");
    expect(doc.role).toBe("worker");
    expect(doc.result_text).toBe("done");
  });

  it("fetchNodeResult rejects an invalid node_id", async () => {
    await expect(api.fetchNodeResult("t1", "inv_001", "../bad")).rejects.toThrow(/invalid node_id/);
  });

  it("fetchNodeResult rejects an invalid task_id", async () => {
    await expect(api.fetchNodeResult("../bad", "inv_001", "worker")).rejects.toThrow(/invalid task_id/);
  });

  it("fetchNodeResult rejects an invalid inv_id", async () => {
    await expect(api.fetchNodeResult("t1", "bad", "worker")).rejects.toThrow(/invalid inv_id/);
  });

  it("fetchNodeConfig returns the node config doc", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001/agents/worker/config", () =>
        HttpResponse.json({
          system_prompt: "be helpful",
          user_text: "do something",
          allowed_tools: ["Bash"],
          max_turns: 10,
          permission_mode: "auto",
          model: "sonnet",
          turns_source: "adaptive",
          derived_max_turns: 8,
        }),
      ),
    );
    const doc = await api.fetchNodeConfig("t1", "inv_001", "worker");
    expect(doc.system_prompt).toBe("be helpful");
    expect(doc.allowed_tools).toEqual(["Bash"]);
    expect(doc.turns_source).toBe("adaptive");
    expect(doc.derived_max_turns).toBe(8);
  });

  it("fetchNodeConfig rejects an invalid node_id", async () => {
    await expect(api.fetchNodeConfig("t1", "inv_001", "../bad")).rejects.toThrow(/invalid node_id/);
  });

  it("fetchNodeConfig rejects an invalid task_id", async () => {
    await expect(api.fetchNodeConfig("../bad", "inv_001", "worker")).rejects.toThrow(/invalid task_id/);
  });

  it("fetchNodeConfig rejects an invalid inv_id", async () => {
    await expect(api.fetchNodeConfig("t1", "bad", "worker")).rejects.toThrow(/invalid inv_id/);
  });
});

describe("api.getHandovers", () => {
  const HANDOVER = {
    schema_version: "1.6",
    handover_id: "h1",
    handover_type: "forward",
    task_id: "ws1",
    workspace_id: "ws1",
    from_team: "research",
    from_invocation_id: "inv_001",
    to_team_suggestions: ["architecture"],
    iteration: 1,
    revisit_of: null,
    timestamp_utc: "2026-05-19T10:00:00Z",
    next_hop: null,
    decisions: [],
    open_questions: [],
    assumptions: [],
    constraints_inherited: [],
    artifacts_produced: [],
    full_synthesis: "done",
    forbidden_actions: ["delete files"],
    prior_attempts_summary: "tried once",
    pr_number: 42,
    files_to_edit: ["alan/server.py"],
    test_scenarios: [
      {
        id: "ts1",
        description: "basic flow",
        given: "a workspace",
        when: "dispatched",
        then: "succeeds",
      },
    ],
  };

  it("calls GET /api/workspaces/<id>/handovers and returns array", async () => {
    server.use(
      http.get("/api/workspaces/ws1/handovers", () =>
        HttpResponse.json([HANDOVER]),
      ),
    );
    const res = await api.getHandovers("ws1");
    expect(res).toHaveLength(1);
    expect(res[0]!.handover_id).toBe("h1");
    expect(res[0]!.schema_version).toBe("1.6");
  });

  it("returned handover includes optional fields (forbidden_actions, prior_attempts_summary, …)", async () => {
    server.use(
      http.get("/api/workspaces/ws1/handovers", () =>
        HttpResponse.json([HANDOVER]),
      ),
    );
    const [h] = await api.getHandovers("ws1");
    expect(h!.forbidden_actions).toEqual(["delete files"]);
    expect(h!.prior_attempts_summary).toBe("tried once");
    expect(h!.pr_number).toBe(42);
    expect(h!.files_to_edit).toEqual(["alan/server.py"]);
    expect(h!.test_scenarios).toHaveLength(1);
    expect(h!.test_scenarios![0]!.id).toBe("ts1");
  });

  it("rejects invalid task ids without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.getHandovers("BAD ID")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("propagates backend errors", async () => {
    server.use(
      http.get("/api/workspaces/missing/handovers", () =>
        HttpResponse.json({ detail: "workspace not found: missing" }, { status: 404 }),
      ),
    );
    await expect(api.getHandovers("missing")).rejects.toThrow(
      "404: workspace not found: missing",
    );
  });
});

describe("api.getDelivery", () => {
  it("calls GET /api/workspaces/<id>/delivery and returns parsed body", async () => {
    const payload = {
      pr_number: 42,
      repo: "owner/repo",
      pr_url: "https://github.com/owner/repo/pull/42",
      pr_title: "Add delivery endpoint",
      merged_at: "2026-05-20T10:00:00Z",
      files_changed: ["alan/delivery.py"],
      commits: [{ sha: "abc123", message: "feat: add delivery endpoint" }],
    };
    server.use(
      http.get("/api/workspaces/ws1/delivery", () => HttpResponse.json(payload)),
    );
    const res = await api.getDelivery("ws1");
    expect(res.pr_number).toBe(42);
    expect(res.repo).toBe("owner/repo");
    expect(res.pr_title).toBe("Add delivery endpoint");
    expect(res.files_changed).toEqual(["alan/delivery.py"]);
    expect(res.commits).toHaveLength(1);
    expect(res.commits![0]?.sha).toBe("abc123");
  });

  it("rejects invalid task_id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.getDelivery("BAD ID")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("propagates backend errors", async () => {
    server.use(
      http.get("/api/workspaces/active-ws/delivery", () =>
        HttpResponse.json({ detail: "workspace not completed" }, { status: 409 }),
      ),
    );
    await expect(api.getDelivery("active-ws")).rejects.toThrow(
      "409: workspace not completed",
    );
  });
});

describe("Task and TaskStatus types", () => {
  it("Task has expected shape at runtime", () => {
    const task: Task = makeWorkspaceTask({
      id: "task-001", depends_on: ["task-000"], team: "implementation", status: "pending" satisfies TaskStatus,
    });
    expect(task.id).toBe("task-001");
    expect(task.depends_on).toEqual(["task-000"]);
    expect(task.team).toBe("implementation");
    expect(task.status).toBe("pending");
  });
});

describe("api.updateWorkspaceNote", () => {
  it("PUTs note text and returns updated note", async () => {
    let captured: unknown = null;
    server.use(
      http.put("/api/workspaces/ws-1/note", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ note: "my memo" });
      }),
    );
    const res = await api.updateWorkspaceNote("ws-1", "my memo");
    expect(res.note).toBe("my memo");
    expect(captured).toEqual({ note: "my memo" });
  });

  it("PUTs null to clear the note", async () => {
    let captured: unknown = null;
    server.use(
      http.put("/api/workspaces/ws-1/note", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ note: null });
      }),
    );
    const res = await api.updateWorkspaceNote("ws-1", null);
    expect(res.note).toBeNull();
    expect(captured).toEqual({ note: null });
  });

  it("rejects invalid task_id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.updateWorkspaceNote("BAD ID", "x")).rejects.toThrow(/invalid task_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("propagates backend errors", async () => {
    server.use(
      http.put("/api/workspaces/ws-x/note", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    await expect(api.updateWorkspaceNote("ws-x", "hi")).rejects.toThrow("404: not found");
  });
});


describe("api.advanceProjectRoadmap", () => {
  it("posts to the roadmap advance endpoint with no body and returns the started item", async () => {
    let called = false;
    server.use(
      http.post("/api/projects/proj-1/roadmap/advance", () => {
        called = true;
        return HttpResponse.json(
          { task_id: "ws-next", item_id: "item-2", item_title: "Item Two" },
          { status: 201 },
        );
      }),
    );
    const res = await api.advanceProjectRoadmap("proj-1");
    expect(called).toBe(true);
    expect(res).toEqual({ task_id: "ws-next", item_id: "item-2", item_title: "Item Two" });
  });

  it("propagates a 409 when there is no un-attempted roadmap item left", async () => {
    server.use(
      http.post("/api/projects/proj-1/roadmap/advance", () =>
        HttpResponse.json(
          { detail: "no un-attempted roadmap item to advance to" },
          { status: 409 },
        ),
      ),
    );
    await expect(api.advanceProjectRoadmap("proj-1")).rejects.toThrow(
      "409: no un-attempted roadmap item to advance to",
    );
  });

  it("rejects an invalid project_id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.advanceProjectRoadmap("BAD ID")).rejects.toThrow(/invalid project_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.getRoadmapNextItemProposal", () => {
  it("GETs the next-item-proposal sub-resource and returns the proposal plus budget state", async () => {
    let method = "";
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", ({ request }) => {
        method = request.method;
        return HttpResponse.json({
          proposal: {
            fingerprint: null,
            workspace_id: null,
            task_id: null,
            project_id: "proj-1",
            engine_key: "DejvMatikar/alan",
            title: "Next: Ship the widget",
            prepared_context: "Build the widget.",
            recommendation: "All prior roadmap items have delivered — start 'Ship the widget' next.",
            actions: [
              {
                kind: "direct_dispatch",
                label: "Start: Ship the widget",
                team: null,
                instructions: null,
                url: "/api/projects/proj-1/roadmap/advance",
              },
            ],
            updated_at: "2026-07-08T00:00:00Z",
            autonomy: "propose",
          },
          budget_state: "normal",
        });
      }),
    );
    const res = await api.getRoadmapNextItemProposal("proj-1");
    expect(method).toBe("GET");
    expect(res.proposal?.title).toBe("Next: Ship the widget");
    expect(res.proposal?.actions[0]?.url).toBe("/api/projects/proj-1/roadmap/advance");
    expect(res.budget_state).toBe("normal");
  });

  it("returns a null proposal when there is nothing to propose", async () => {
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () =>
        HttpResponse.json({ proposal: null, budget_state: "normal" }),
      ),
    );
    const res = await api.getRoadmapNextItemProposal("proj-1");
    expect(res.proposal).toBeNull();
  });

  it("rejects an invalid project_id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.getRoadmapNextItemProposal("BAD ID")).rejects.toThrow(/invalid project_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.listProjects", () => {
  it("calls the bare /api/projects URL when no opts are given", async () => {
    let capturedInclude: string | null = "not-checked";
    server.use(
      http.get("/api/projects", ({ request }) => {
        capturedInclude = new URL(request.url).searchParams.get("include");
        return HttpResponse.json([]);
      }),
    );
    const res = await api.listProjects();
    expect(res).toEqual([]);
    expect(capturedInclude).toBeNull();
  });

  it("appends ?include=archived when includeArchived is true", async () => {
    let capturedInclude: string | null = null;
    server.use(
      http.get("/api/projects", ({ request }) => {
        capturedInclude = new URL(request.url).searchParams.get("include");
        return HttpResponse.json([]);
      }),
    );
    await api.listProjects({ includeArchived: true });
    expect(capturedInclude).toBe("archived");
  });
});

describe("api.createProject", () => {
  it("posts title and doc_ref and returns the created project", async () => {
    server.use(
      http.post("/api/projects", async ({ request }) => {
        const body = (await request.json()) as { title: string; doc_ref: string | null };
        return HttpResponse.json(
          {
            id: "proj-1",
            title: body.title,
            status: "active",
            doc_ref: body.doc_ref,
            created_at: "2026-07-01T00:00:00Z",
            updated_at: "2026-07-01T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    const res = await api.createProject("My Project", "docs/x.md");
    expect(res.id).toBe("proj-1");
    expect(res.doc_ref).toBe("docs/x.md");
  });

  it("a three-arg call serialises intended_engine_key", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/projects", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          {
            id: "proj-2",
            title: "Intent Project",
            status: "active",
            doc_ref: null,
            intended_engine_key: "acme/sprocket",
            created_at: "2026-07-01T00:00:00Z",
            updated_at: "2026-07-01T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    const res = await api.createProject("Intent Project", null, "acme/sprocket");
    expect(captured).toEqual({
      title: "Intent Project",
      doc_ref: null,
      intended_engine_key: "acme/sprocket",
    });
    expect(res.id).toBe("proj-2");
  });

  it("a two-arg call omits intended_engine_key entirely", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/projects", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          {
            id: "proj-3",
            title: "No Intent Project",
            status: "active",
            doc_ref: null,
            created_at: "2026-07-01T00:00:00Z",
            updated_at: "2026-07-01T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    await api.createProject("No Intent Project", null);
    expect(captured).toEqual({ title: "No Intent Project", doc_ref: null });
  });
});

describe("api.getProject", () => {
  it("returns the project detail payload", async () => {
    server.use(
      http.get("/api/projects/proj-1", () =>
        HttpResponse.json({
          id: "proj-1",
          title: "My Project",
          status: "active",
          doc_ref: null,
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
          workspaces: [],
        }),
      ),
    );
    const res = await api.getProject("proj-1");
    expect(res.id).toBe("proj-1");
    expect(res.workspaces).toEqual([]);
  });

  it("rejects an invalid project_id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.getProject("BAD ID")).rejects.toThrow(/invalid project_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.updateProject", () => {
  it("serializes engine_key into the PATCH body when supplied", async () => {
    let captured: unknown = null;
    server.use(
      http.patch("/api/projects/proj-1", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          id: "proj-1",
          title: "My Project",
          status: "active",
          doc_ref: null,
          autonomy: "propose",
          budget_usd: null,
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        });
      }),
    );
    const res = await api.updateProject("proj-1", { engine_key: "acme/widget" });
    expect(res.id).toBe("proj-1");
    expect(captured).toEqual({ engine_key: "acme/widget" });
  });

  it("omits engine_key from the PATCH body when not supplied", async () => {
    let captured: unknown = null;
    server.use(
      http.patch("/api/projects/proj-1", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          id: "proj-1",
          title: "New Title",
          status: "active",
          doc_ref: null,
          autonomy: "propose",
          budget_usd: null,
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        });
      }),
    );
    await api.updateProject("proj-1", { title: "New Title" });
    expect(captured).toEqual({ title: "New Title" });
  });
});

describe("api.completeProject", () => {
  it("posts default force=false and empty reason, returning the completed project", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/projects/proj-1/complete", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          id: "proj-1",
          title: "My Project",
          status: "completed",
          doc_ref: "project.md",
          autonomy: "propose",
          budget_usd: null,
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-12T00:00:00Z",
        });
      }),
    );
    const res = await api.completeProject("proj-1");
    expect(captured).toEqual({ force: false, reason: "" });
    expect(res.status).toBe("completed");
  });

  it("posts force=true with a reason when provided", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/projects/proj-1/complete", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          id: "proj-1",
          title: "My Project",
          status: "completed",
          doc_ref: null,
          autonomy: "propose",
          budget_usd: null,
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-12T00:00:00Z",
        });
      }),
    );
    await api.completeProject("proj-1", { force: true, reason: "shipping anyway" });
    expect(captured).toEqual({ force: true, reason: "shipping anyway" });
  });

  it("propagates a 409 when the project is not ready", async () => {
    server.use(
      http.post("/api/projects/proj-1/complete", () =>
        HttpResponse.json({ detail: "project_not_ready" }, { status: 409 }),
      ),
    );
    await expect(api.completeProject("proj-1")).rejects.toThrow("409: project_not_ready");
  });

  it("propagates a 409 when the project is already completed", async () => {
    server.use(
      http.post("/api/projects/proj-1/complete", () =>
        HttpResponse.json({ detail: "project_already_completed" }, { status: 409 }),
      ),
    );
    await expect(api.completeProject("proj-1")).rejects.toThrow(
      "409: project_already_completed",
    );
  });

  it("rejects an invalid project_id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.completeProject("BAD ID")).rejects.toThrow(/invalid project_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.getProjectDoc", () => {
  it("returns the four parsed anchor fields", async () => {
    server.use(
      http.get("/api/projects/proj-1/doc", () =>
        HttpResponse.json({
          vision: "Ship it",
          scope: "In: x. Out: y.",
          decision_log: "Adopted the spine.",
          discovered_facts: "Found a thing.",
        }),
      ),
    );
    const res = await api.getProjectDoc("proj-1");
    expect(res.vision).toBe("Ship it");
    expect(res.scope).toBe("In: x. Out: y.");
    expect(res.decision_log).toBe("Adopted the spine.");
    expect(res.discovered_facts).toBe("Found a thing.");
  });

  it("rejects an invalid project_id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.getProjectDoc("BAD ID")).rejects.toThrow(/invalid project_id/);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.proposeProjectAmendment", () => {
  it("posts vision/scope/decision_entry and returns the pending fingerprint", async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post("/api/projects/proj-1/amendments", async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(
          { fingerprint: "abc123", project_id: "proj-1", proposed_at: "2026-07-08T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    const res = await api.proposeProjectAmendment("proj-1", "V", "S", "D");
    expect(res.fingerprint).toBe("abc123");
    expect(capturedBody).toEqual({ vision: "V", scope: "S", decision_entry: "D" });
  });

  it("rejects an invalid project_id without sending a request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(api.proposeProjectAmendment("BAD ID", "V", "S", "D")).rejects.toThrow(
      /invalid project_id/,
    );
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("api.getEnginePresetConfig", () => {
  it("passes owner/repo as query params and returns the built config", async () => {
    let capturedUrl = "";
    server.use(
      http.get("/api/engine-presets/python/config", ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({
          ...MINIMAL_ENGINE_CONFIG,
          preset: "python",
          forge: {
            repo: { owner: "acme", repo: "widget" },
            credential_env_name: "ACME_WIDGET_GH_TOKEN",
            authoritative_checks: [],
          },
        });
      }),
    );
    const cfg = await api.getEnginePresetConfig("python", "acme", "widget");
    const params = new URL(capturedUrl).searchParams;
    expect(params.get("owner")).toBe("acme");
    expect(params.get("repo")).toBe("widget");
    expect(cfg.forge?.credential_env_name).toBe("ACME_WIDGET_GH_TOKEN");
    expect(cfg.posture).toBe("observe");
  });

  it("throws with parsed detail on 404 for an unknown preset", async () => {
    server.use(
      http.get("/api/engine-presets/cobol/config", () =>
        HttpResponse.json({ detail: "preset not found" }, { status: 404 }),
      ),
    );
    await expect(api.getEnginePresetConfig("cobol", "acme", "widget")).rejects.toThrow(
      "404: preset not found",
    );
  });
});

describe("api.createEngine", () => {
  it("posts the config to /api/engines with the control-token header and returns the persisted config", async () => {
    writeControlToken("my-secret-token");
    let captured: unknown = null;
    let capturedHeader: string | null = null;
    server.use(
      http.post("/api/engines", async ({ request }) => {
        captured = await request.json();
        capturedHeader = request.headers.get(CONTROL_TOKEN_HEADER);
        return HttpResponse.json(
          { ...MINIMAL_ENGINE_CONFIG, preset: "python" },
          { status: 201 },
        );
      }),
    );
    const created = await api.createEngine({ ...MINIMAL_ENGINE_CONFIG, preset: "python" });
    expect(captured).toEqual({ ...MINIMAL_ENGINE_CONFIG, preset: "python" });
    expect(capturedHeader).toBe("my-secret-token");
    expect(created.preset).toBe("python");
  });

  it("throws with parsed detail on 422 (e.g. missing credential record)", async () => {
    server.use(
      http.post("/api/engines", () =>
        HttpResponse.json({ detail: "no secret record for 'acme/widget:transport'" }, { status: 422 }),
      ),
    );
    await expect(api.createEngine(MINIMAL_ENGINE_CONFIG)).rejects.toThrow(
      "422: no secret record for 'acme/widget:transport'",
    );
  });
});

describe("api.listSecrets / createSecret / deleteSecret", () => {
  it("listSecrets returns the secrets array with no value field", async () => {
    server.use(
      http.get("/api/secrets", () =>
        HttpResponse.json({
          secrets: [
            {
              name: "acme/widget:transport",
              fingerprint: "abc123",
              last_verified: null,
              expires_at: null,
              expiry_warned_at: null,
            },
          ],
        }),
      ),
    );
    const secrets = await api.listSecrets();
    expect(secrets).toEqual([
      {
        name: "acme/widget:transport",
        fingerprint: "abc123",
        last_verified: null,
        expires_at: null,
        expiry_warned_at: null,
      },
    ]);
  });

  it("createSecret posts the body and attaches the control-token header", async () => {
    localStorage.clear();
    writeControlToken("my-secret-token");
    let captured: unknown = null;
    let capturedHeader: string | null = null;
    server.use(
      http.post("/api/secrets", async ({ request }) => {
        captured = await request.json();
        capturedHeader = request.headers.get(CONTROL_TOKEN_HEADER);
        return HttpResponse.json(
          {
            name: "acme/widget:transport",
            fingerprint: "abc123",
            last_verified: null,
            expires_at: null,
            expiry_warned_at: null,
          },
          { status: 201 },
        );
      }),
    );
    const created = await api.createSecret({
      engine: "acme/widget",
      purpose: "transport",
      value: "ghp_abc",
    });
    expect(captured).toEqual({
      engine: "acme/widget",
      purpose: "transport",
      value: "ghp_abc",
    });
    expect(capturedHeader).toBe("my-secret-token");
    expect(created.name).toBe("acme/widget:transport");
    expect((created as unknown as Record<string, unknown>).value).toBeUndefined();
  });

  it("createSecret throws with parsed detail on 422", async () => {
    server.use(
      http.post("/api/secrets", () =>
        HttpResponse.json({ detail: "value must not be empty" }, { status: 422 }),
      ),
    );
    await expect(
      api.createSecret({ engine: "acme/widget", purpose: "transport", value: "" }),
    ).rejects.toThrow("422: value must not be empty");
  });

  it("deleteSecret sends the body and the control-token header, and resolves on 204", async () => {
    localStorage.clear();
    writeControlToken("my-secret-token");
    let captured: unknown = null;
    let capturedHeader: string | null = null;
    server.use(
      http.delete("/api/secrets", async ({ request }) => {
        captured = await request.json();
        capturedHeader = request.headers.get(CONTROL_TOKEN_HEADER);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await expect(
      api.deleteSecret({ engine: "acme/widget", purpose: "transport" }),
    ).resolves.toBeUndefined();
    expect(captured).toEqual({ engine: "acme/widget", purpose: "transport" });
    expect(capturedHeader).toBe("my-secret-token");
  });

  it("deleteSecret omits the header when no token is stored", async () => {
    localStorage.clear();
    let capturedHeader: string | null = "not-checked";
    server.use(
      http.delete("/api/secrets", ({ request }) => {
        capturedHeader = request.headers.get(CONTROL_TOKEN_HEADER);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await api.deleteSecret({ engine: "acme/widget", purpose: "transport" });
    expect(capturedHeader).toBeNull();
  });

  it("deleteSecret throws with parsed detail on 404", async () => {
    server.use(
      http.delete("/api/secrets", () =>
        HttpResponse.json({ detail: "secret not found" }, { status: 404 }),
      ),
    );
    await expect(
      api.deleteSecret({ engine: "acme/widget", purpose: "transport" }),
    ).rejects.toThrow("404: secret not found");
  });
});

describe("api.checkCollision", () => {
  it("sends brief, chat_family, and session_id with no exclude_task_id field", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/collision-check", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ state: "green", sentence: "", match: null });
      }),
    );
    await api.checkCollision("brief text", { chatFamily: "build", sessionId: "sid-1" });
    expect(captured).toEqual({
      brief: "brief text",
      chat_family: "build",
      session_id: "sid-1",
    });
  });

  it("omits chatFamily and sessionId when not provided", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/collision-check", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ state: "green", sentence: "", match: null });
      }),
    );
    await api.checkCollision("brief text");
    expect(captured).toEqual({ brief: "brief text" });
  });
});

describe("api.subscribeEvents", () => {
  it("delivers known kinds and filters unknown ones", async () => {
    const body =
      'event: changed\ndata: {"kinds":["engines","bogus"]}\n\n' +
      'event: other\ndata: {"kinds":["health"]}\n\n' +
      'event: changed\ndata: {"kinds":["workspace_list","health"]}\n\n';
    server.use(
      http.get("/api/events", () =>
        new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } }),
      ),
    );
    const received: string[][] = [];
    let disconnects = 0;
    api.subscribeEvents({
      onChanged: (kinds) => received.push([...kinds]),
      onDisconnect: () => {
        disconnects += 1;
      },
    });
    await vi.waitFor(() => {
      expect(disconnects).toBe(1);
    });
    expect(received).toEqual([["engines"], ["workspace_list", "health"]]);
  });

  it("ignores malformed, non-object, and array-less payloads", async () => {
    const body =
      "event: changed\ndata: not json{{{\n\n" +
      "event: changed\ndata: 42\n\n" +
      'event: changed\ndata: {"kinds":"engines"}\n\n' +
      'event: changed\ndata: {"kinds":[]}\n\n';
    server.use(
      http.get("/api/events", () =>
        new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } }),
      ),
    );
    const received: string[][] = [];
    let disconnects = 0;
    api.subscribeEvents({
      onChanged: (kinds) => received.push([...kinds]),
      onDisconnect: () => {
        disconnects += 1;
      },
    });
    await vi.waitFor(() => {
      expect(disconnects).toBe(1);
    });
    expect(received).toEqual([]);
  });

  it("reports a network failure as a single disconnect", async () => {
    server.use(http.get("/api/events", () => HttpResponse.error()));
    let disconnects = 0;
    api.subscribeEvents({
      onChanged: () => undefined,
      onDisconnect: () => {
        disconnects += 1;
      },
    });
    await vi.waitFor(() => {
      expect(disconnects).toBe(1);
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(disconnects).toBe(1);
  });

  it("suppresses the disconnect callback after unsubscribe", async () => {
    server.use(
      http.get("/api/events", () =>
        new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } }),
      ),
    );
    let disconnects = 0;
    const unsubscribe = api.subscribeEvents({
      onChanged: () => undefined,
      onDisconnect: () => {
        disconnects += 1;
      },
    });
    unsubscribe();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(disconnects).toBe(0);
  });
});

describe("api.getWorkspacePrepChat", () => {
  it("passes through the brief field when the archive carries one", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json({
          session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          kind: "build",
          consultant_key: null,
          title: "t",
          created_at: "2026-05-01T00:00:00Z",
          messages: [],
          brief: { content: "distilled", updated_at: "2026-05-01T01:00:00Z" },
        }),
      ),
    );
    const transcript = await api.getWorkspacePrepChat("t1");
    expect(transcript.brief).toEqual({
      content: "distilled",
      updated_at: "2026-05-01T01:00:00Z",
    });
  });

  it("leaves brief undefined when the archive was never briefed", async () => {
    server.use(
      http.get("/api/workspaces/t1/prep-chat", () =>
        HttpResponse.json({
          session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          kind: "build",
          consultant_key: null,
          title: "t",
          created_at: "2026-05-01T00:00:00Z",
          messages: [],
        }),
      ),
    );
    const transcript = await api.getWorkspacePrepChat("t1");
    expect(transcript.brief ?? null).toBeNull();
  });
});

describe("makeChatApi.get redirect-awareness (ArchivedSessionRedirect)", () => {
  const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";

  it("api.getBuildChatSession throws ArchivedSessionRedirect when the session GET redirects", async () => {
    server.use(
      http.get(`/api/build-chat/sessions/${SID}`, ({ request }) =>
        HttpResponse.redirect(
          new URL("/api/workspaces/archived-ws/prep-chat", request.url).toString(),
          303,
        ),
      ),
      http.get("/api/workspaces/archived-ws/prep-chat", () =>
        HttpResponse.json({
          session_id: SID,
          kind: "build",
          consultant_key: null,
          title: "t",
          created_at: "2026-05-01T00:00:00Z",
          messages: [],
        }),
      ),
    );
    let caught: unknown = null;
    try {
      await api.getBuildChatSession(SID);
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ArchivedSessionRedirect);
    expect((caught as ArchivedSessionRedirect).archivedTaskId).toBe("archived-ws");
  });

  it("api.getExploreChatSession throws ArchivedSessionRedirect when the session GET redirects", async () => {
    server.use(
      http.get(`/api/explore-chat/sessions/${SID}`, ({ request }) =>
        HttpResponse.redirect(
          new URL("/api/workspaces/archived-ws-2/prep-chat", request.url).toString(),
          303,
        ),
      ),
      http.get("/api/workspaces/archived-ws-2/prep-chat", () =>
        HttpResponse.json({
          session_id: SID,
          kind: "explore",
          consultant_key: "research",
          title: "t",
          created_at: "2026-05-01T00:00:00Z",
          messages: [],
        }),
      ),
    );
    await expect(api.getExploreChatSession(SID)).rejects.toBeInstanceOf(ArchivedSessionRedirect);
  });

  it("resolves the session normally when the response is not a redirect", async () => {
    server.use(
      http.get(`/api/build-chat/sessions/${SID}`, () =>
        HttpResponse.json({
          session_id: SID,
          project_id: null,
          engine_key: null,
          repo_provision: "not_needed",
          created_at: "2026-05-01T00:00:00Z",
          messages: [],
          in_flight_turn: null,
          brief: null,
        }),
      ),
    );
    const session = await api.getBuildChatSession(SID);
    expect(session.session_id).toBe(SID);
  });

  it("still throws ApiError for a non-redirect error response", async () => {
    server.use(
      http.get(`/api/build-chat/sessions/${SID}`, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    await expect(api.getBuildChatSession(SID)).rejects.toBeInstanceOf(ApiError);
  });

  it("does not throw ArchivedSessionRedirect for a redirect to an unrelated URL", async () => {
    server.use(
      http.get(`/api/build-chat/sessions/${SID}`, ({ request }) =>
        HttpResponse.redirect(new URL("/api/build-chat/sessions", request.url).toString(), 303),
      ),
      http.get("/api/build-chat/sessions", () => HttpResponse.json([])),
    );
    await expect(api.getBuildChatSession(SID)).resolves.not.toBeInstanceOf(
      ArchivedSessionRedirect,
    );
  });
});
