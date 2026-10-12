import { HttpResponse, http } from "msw";

import { repoHealthResponse } from "./healthWindows";

const REPO_STATUS_UNKNOWN = { status: "unknown", detail: "test default: no repo-status mock set up" };

function defaultRepoStatus(slug: string) {
  return {
    slug,
    is_default: slug === "DejvMatikar/alan",
    main_ci: { provenance: REPO_STATUS_UNKNOWN, green_count: null, red_count: null, counted_over: null },
    open_prs: { provenance: REPO_STATUS_UNKNOWN, total: null, authored_by_alan: null },
    cost: { provenance: REPO_STATUS_UNKNOWN, total_cost_usd: null, unknown_count: 0 },
    delivery_time: { provenance: REPO_STATUS_UNKNOWN, by_size: [] },
    last_deploy: { provenance: REPO_STATUS_UNKNOWN, outcome: null, pr_number: null, at: null },
    health: { provenance: REPO_STATUS_UNKNOWN, finding_count: null, verdict: null },
  };
}

function defaultRecurringStatus(slug: string) {
  return {
    slug,
    is_default: slug === "DejvMatikar/alan",
    processes: [],
    service_scope_note: null,
    backup_coverage: {
      provenance: REPO_STATUS_UNKNOWN,
      included_in_last_snapshot: null,
      changed_since_count: null,
      excluded_caveat: "test default: no recurring-status mock set up",
    },
  };
}

export const handlers = [
  http.get(/\/api\/engines\/([^/]+)\/([^/]+)\/status$/, ({ request }) => {
    const match = /\/api\/engines\/([^/]+)\/([^/]+)\/status$/.exec(new URL(request.url).pathname);
    const owner = decodeURIComponent(match?.[1] ?? "");
    const repo = decodeURIComponent(match?.[2] ?? "");
    return HttpResponse.json(defaultRepoStatus(`${owner}/${repo}`));
  }),
  http.get(/\/api\/engines\/([^/]+)\/([^/]+)\/recurring-status$/, ({ request }) => {
    const match = /\/api\/engines\/([^/]+)\/([^/]+)\/recurring-status$/.exec(
      new URL(request.url).pathname,
    );
    const owner = decodeURIComponent(match?.[1] ?? "");
    const repo = decodeURIComponent(match?.[2] ?? "");
    return HttpResponse.json(defaultRecurringStatus(`${owner}/${repo}`));
  }),
  http.get(/\/api\/engines\/([^/]+)\/([^/]+)\/agents\/summary$/, ({ request }) => {
    const match = /\/api\/engines\/([^/]+)\/([^/]+)\/agents\/summary$/.exec(
      new URL(request.url).pathname,
    );
    const owner = decodeURIComponent(match?.[1] ?? "");
    const repo = decodeURIComponent(match?.[2] ?? "");
    return HttpResponse.json({
      engine: `${owner}/${repo}`,
      total: 0,
      by_model: [],
      by_effort: [],
      by_kind: [],
      warnings: 0,
      infos: 0,
      errors: 0,
    });
  }),
  http.get("/api/health", () =>
    HttpResponse.json({
      ok: true,
      queue_paused: false,
      paused_until: null,
      health_state: null,
      quality_metrics: {
        build_success_rate: null,
        in_flight_build_count: null,
        first_pass_approval_rate: null,
        mean_fix_cycles_to_merge: null,
        sample_size: null,
        computed_at: null,
        format_error_rate: null,
        format_error_sample: 0,
        format_error_low_data: true,
        recovery_rate: null,
        recovery_sample: 0,
        recovery_low_data: true,
      },
    }),
  ),
  http.get("/api/health/repos", () => HttpResponse.json(repoHealthResponse())),
  http.get("/api/health/digest", () =>
    HttpResponse.json({ recovery: null, residue: [], digest_overdue: false }),
  ),
  http.post("/api/queue/resume", () =>
    HttpResponse.json({ ok: true, queue_paused: false, paused_until: null }),
  ),
  http.get("/api/driver-loops", () => HttpResponse.json({ supervised: false, loops: [] })),
  http.put(/\/api\/workspaces\/[^/]+\/note$/, async ({ request }) => {
    const body = (await request.json()) as { note: string | null };
    return HttpResponse.json({ note: body.note });
  }),
  http.get(/\/api\/workspaces\/[^/]+\/invocations$/, () => HttpResponse.json([])),
  http.get(/\/api\/workspaces\/[^/]+\/handovers$/, () => HttpResponse.json([])),
  http.get(/\/api\/workspaces\/[^/]+\/events$/, () =>
    new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } }),
  ),
  http.get(/\/api\/workspaces\/[^/]+\/chat\/sessions\/[^/]+\/events$/, () =>
    new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } }),
  ),
  http.get("/api/events", () =>
    new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } }),
  ),
  http.get(/\/api\/workspaces\/[^/]+\/invocations\/[^/]+$/, () =>
    HttpResponse.json({
      schema_version: "1.0",
      id: "stub",
      namespace: "stub",
      team: null,
      status: "running",
      started_at: "2026-05-12T08:00:00Z",
      completed_at: null,
      tasks: [],
    }),
  ),
  http.get("/api/build-chat/sessions", () => HttpResponse.json([])),
  http.post("/api/build-chat/sessions", () =>
    HttpResponse.json(
      { session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" },
      { status: 201 },
    ),
  ),
  http.get(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
    HttpResponse.json({
      session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      created_at: "2026-05-01T00:00:00Z",
      messages: [],
      in_flight_turn: null,
      brief: null,
    }),
  ),
  http.get(/\/api\/build-chat\/sessions\/[^/]+\/events$/, () =>
    new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } }),
  ),
  http.post(/\/api\/build-chat\/sessions\/[^/]+\/interrupt$/, () => new HttpResponse(null, { status: 204 })),
  http.get("/api/explore-chat/sessions", () => HttpResponse.json([])),
  http.post("/api/explore-chat/sessions", () =>
    HttpResponse.json(
      { session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" },
      { status: 201 },
    ),
  ),
  http.get(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
    HttpResponse.json({
      session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      consultant_key: "brainstorming",
      created_at: "2026-05-01T00:00:00Z",
      messages: [],
      in_flight_turn: null,
      brief: null,
    }),
  ),
  http.get(/\/api\/explore-chat\/sessions\/[^/]+\/events$/, () =>
    new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } }),
  ),
  http.post(/\/api\/explore-chat\/sessions\/[^/]+\/interrupt$/, () => new HttpResponse(null, { status: 204 })),
  http.post("/api/collision-check", () =>
    HttpResponse.json({
      state: "green",
      sentence: "No overlapping work found.",
      match: null,
    }),
  ),
  http.get("/api/explore-chat/consultants", () =>
    HttpResponse.json([
      { key: "brainstorming", label: "Brainstorming", description: "Diverge.", team: "brainstorming" },
      { key: "research", label: "Research", description: "Research.", team: "research" },
      { key: "architecture", label: "Architecture", description: "Architecture.", team: "architecture" },
      { key: "opportunity_scout", label: "Opportunity scout", description: "Scout.", team: "opportunity_scout" },
    ]),
  ),
  http.get("/api/engines", () =>
    HttpResponse.json({
      default_engine_key: "DejvMatikar/alan",
      engines: [{ repo: { owner: "DejvMatikar", repo: "alan" }, posture: "autonomous" }],
    }),
  ),
  http.get("/api/engines/sources", () => HttpResponse.json({ sources: [] })),
  http.get("/api/secrets", () => HttpResponse.json({ secrets: [] })),
  http.get("/api/control-token/status", () =>
    HttpResponse.json({ state: "active", deadline: "2099-01-01T00:00:00+00:00" }),
  ),
  http.get("/api/projects", () => HttpResponse.json([])),
  http.get(/\/api\/projects\/[^/]+\/roadmap\/next-item-proposal$/, () =>
    HttpResponse.json({ proposal: null, budget_state: "normal" }),
  ),
  http.get("/api/teams", () =>
    HttpResponse.json({
      teams: [
        {
          name: "architecture",
          manually_dispatchable: true,
          initial_dispatchable: true,
          role_summary: "Design.",
        },
        {
          name: "brainstorming",
          manually_dispatchable: true,
          initial_dispatchable: true,
          role_summary: "Diverge.",
        },
        {
          name: "decomposer",
          manually_dispatchable: false,
          initial_dispatchable: false,
          role_summary: "Split-vs-keep.",
        },
        {
          name: "implementation",
          manually_dispatchable: true,
          initial_dispatchable: true,
          role_summary: "Code.",
        },
        {
          name: "implementation_fix",
          manually_dispatchable: true,
          initial_dispatchable: false,
          role_summary: "Address Copilot.",
        },
        {
          name: "implementation_plan",
          manually_dispatchable: true,
          initial_dispatchable: true,
          role_summary: "Plan.",
        },
        {
          name: "maintenance",
          manually_dispatchable: true,
          initial_dispatchable: true,
          role_summary: "Upkeep.",
        },
        {
          name: "opportunity_scout",
          manually_dispatchable: true,
          initial_dispatchable: true,
          role_summary: "Scout.",
        },
        {
          name: "pr_merge",
          manually_dispatchable: true,
          initial_dispatchable: false,
          role_summary: "Merge.",
        },
        {
          name: "research",
          manually_dispatchable: true,
          initial_dispatchable: true,
          role_summary: "Research.",
        },
        {
          name: "verification",
          manually_dispatchable: true,
          initial_dispatchable: true,
          role_summary: "Verify.",
        },
      ],
    }),
  ),
];
