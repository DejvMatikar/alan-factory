import type { Project, ProjectDetail, ProjectListEntry } from "../api";

const CREATED_AT = "2026-01-01T00:00:00Z";

export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "p1",
    title: "P1",
    status: "active",
    doc_ref: null,
    autonomy: "propose",
    budget_usd: null,
    engine_key: null,
    intended_engine_key: null,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    ...overrides,
  };
}

export function makeProjectDetail(overrides: Partial<ProjectDetail> = {}): ProjectDetail {
  return {
    id: "p1",
    title: "P1",
    status: "active",
    doc_ref: null,
    autonomy: "propose",
    budget_usd: null,
    engine_key: null,
    intended_engine_key: null,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    workspaces: [],
    ready_to_complete: false,
    roadmap: [],
    budget: null,
    ...overrides,
  };
}

export function makeProjectListEntry(overrides: Partial<ProjectListEntry> = {}): ProjectListEntry {
  const id = overrides.id ?? "p1";
  return {
    id,
    title: id,
    status: "active",
    doc_ref: null,
    autonomy: "propose",
    budget_usd: null,
    engine_key: null,
    intended_engine_key: null,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    workspace_count: 0,
    total_spend_usd: 0,
    roadmap_items_total: 0,
    roadmap_items_delivered: 0,
    ...overrides,
  };
}
