import type { Handover, Task, WorkspaceDetail, WorkspaceSummary } from "../api";

export function makeWorkspaceTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    depends_on: [],
    team: null,
    status: "pending",
    injection_depth: 0,
    planning_state: "detailed",
    scenarios_waived: false,
    ...overrides,
  };
}

export function makeHandover(overrides: Partial<Handover> = {}): Handover {
  return {
    schema_version: "1.23",
    handover_id: "h1",
    handover_type: "forward",
    task_id: "t1",
    workspace_id: "t1",
    from_team: "research",
    from_invocation_id: "inv_001",
    to_team_suggestions: [],
    iteration: 1,
    revisit_of: null,
    timestamp_utc: "2026-05-12T08:00:00Z",
    next_hop: null,
    decisions: [],
    open_questions: [],
    assumptions: [],
    constraints_inherited: [],
    artifacts_produced: [],
    full_synthesis: "summary",
    forbidden_actions: [],
    prior_attempts_summary: null,
    pr_number: null,
    files_to_edit: [],
    test_scenarios: [],
    dependency_bot_authorized: false,
    handover_chain_id: "",
    scenarios_waived: false,
    ...overrides,
  };
}

export function makeWorkspaceSummary(overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return {
    task_id: "task-1",
    title: "t",
    status: "active",
    team_runs_total: 0,
    invocations_total: 0,
    total_cost_usd: null,
    updated_at: "2026-07-01T00:00:00Z",
    created_at: "2026-07-01T00:00:00Z",
    latest_team: null,
    latest_invocation_status: null,
    tasks_total: 0,
    tasks_done: 0,
    operator_link: false,
    ...overrides,
  };
}

export function makeWorkspaceDetail(overrides: Partial<WorkspaceDetail> = {}): WorkspaceDetail {
  return {
    task_id: "t1",
    title: "T1",
    description: "d",
    status: "active",
    revisit_counts: {},
    created_at: "2026-05-12T08:00:00Z",
    updated_at: "2026-05-12T08:00:00Z",
    history: [],
    latest_handover: null,
    has_running_job: false,
    has_resumable_failed_job: false,
    requeue_count: 0,
    tasks: [],
    scope_audit: [],
    requirements: null,
    total_cost_usd: null,
    pr_number: null,
    unproven_clause_ids: [],
    unproven_clause_count: 0,
    operator_link: false,
    ...overrides,
  };
}
