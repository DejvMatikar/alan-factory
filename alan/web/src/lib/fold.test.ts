import { describe, expect, it } from "vitest";

import type { EnginePosture, EngineSummary, ProjectListEntry, UnblockRecord, WorkspaceSummary } from "../api";
import { makeEngineSummary, STUB_DEFAULT_ENGINE_KEY } from "../test/engineFixtures";
import { makeWorkspaceSummary } from "../test/workspaceFixtures";
import { SILENCE_THRESHOLD_MS } from "./unblockView";
import {
  aggregateStateForStatuses,
  buildRepoSections,
  buildUnplacedProjectSection,
  compareRepoEntries,
  engineKeyMatchesRepo,
  isRepoSectionEmpty,
  liveProjectIdSet,
  projectPlacementKey,
  registeredRepoEntries,
  repoHeaderModel,
  repoSectionKey,
  sumActiveSeconds,
  switcherEntries,
  titleCaseRepoName,
  UNPLACED_SECTION_KEY,
  UNPLACED_SECTION_TITLE,
  type RepoSectionModel,
} from "./fold";

function ws(overrides: Partial<WorkspaceSummary> & Pick<WorkspaceSummary, "task_id">): WorkspaceSummary {
  return makeWorkspaceSummary({ title: overrides.task_id, project_id: null, engine_key: null, ...overrides });
}

function project(overrides: Partial<ProjectListEntry> & Pick<ProjectListEntry, "id">): ProjectListEntry {
  return {
    title: overrides.id,
    status: "active",
    doc_ref: null,
    autonomy: "propose",
    budget_usd: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    workspace_count: 0,
    total_spend_usd: 0,
    engine_key: null,
    roadmap_items_total: 0,
    roadmap_items_delivered: 0,
    ...overrides,
  };
}

function engine(owner: string, repo: string, posture: EnginePosture = "autonomous"): EngineSummary {
  return makeEngineSummary({ repo: { owner, repo }, posture });
}

const NOW = 1_800_000_000_000;

function unblock(overrides: Partial<UnblockRecord> = {}): UnblockRecord {
  return {
    shape: "stale_review",
    attempts: 1,
    ceiling: 5,
    outcome: "not_yet",
    summary: "waiting · stale_review · re-probing · 2026-09-19T11:55Z",
    issue_url: null,
    at: NOW / 1000,
    action: null,
    reason: null,
    authority_holder: null,
    ...overrides,
  };
}

describe("aggregateStateForStatuses", () => {
  it("is needs_you when any workspace is awaiting_input", () => {
    expect(
      aggregateStateForStatuses([{ status: "completed" }, { status: "awaiting_input" }], NOW),
    ).toBe("needs_you");
  });

  it("is needs_you when any workspace is escalated, even alongside active", () => {
    expect(aggregateStateForStatuses([{ status: "active" }, { status: "escalated" }], NOW)).toBe(
      "needs_you",
    );
  });

  it("is executing when any workspace is active with no needs-you", () => {
    expect(aggregateStateForStatuses([{ status: "completed" }, { status: "active" }], NOW)).toBe(
      "executing",
    );
  });

  it("is idle when nothing needs attention or is active", () => {
    expect(
      aggregateStateForStatuses(
        [{ status: "completed" }, { status: "archived" }, { status: "paused" }],
        NOW,
      ),
    ).toBe("idle");
  });

  it("is idle for an empty status list", () => {
    expect(aggregateStateForStatuses([], NOW)).toBe("idle");
  });

  it("stops firing for a park the host is actively waiting on", () => {
    expect(
      aggregateStateForStatuses(
        [{ status: "awaiting_input", unblock: unblock({ outcome: "not_yet" }) }],
        NOW,
      ),
    ).toBe("idle");
  });

  it("stops firing for a park the host has just acted on", () => {
    expect(
      aggregateStateForStatuses(
        [{ status: "escalated", unblock: unblock({ outcome: "acted" }) }],
        NOW,
      ),
    ).toBe("idle");
  });

  it("keeps firing for a park the host reported as needing a human", () => {
    expect(
      aggregateStateForStatuses(
        [{ status: "awaiting_input", unblock: unblock({ outcome: "needs_human" }) }],
        NOW,
      ),
    ).toBe("needs_you");
  });

  it("keeps firing for a host-held park past the silence threshold, even with no report", () => {
    const stale = unblock({ outcome: "not_yet", at: (NOW - SILENCE_THRESHOLD_MS - 60_000) / 1000 });
    expect(
      aggregateStateForStatuses([{ status: "awaiting_input", unblock: stale }], NOW),
    ).toBe("needs_you");
  });

  it("still fires for a parked workspace with no unblock record at all", () => {
    expect(aggregateStateForStatuses([{ status: "awaiting_input" }], NOW)).toBe("needs_you");
  });

  it("defaults nowMs to Date.now() when omitted", () => {
    expect(aggregateStateForStatuses([{ status: "completed" }])).toBe("idle");
  });
});

describe("repoSectionKey", () => {
  it("folds null to the default slug", () => {
    expect(repoSectionKey(null, STUB_DEFAULT_ENGINE_KEY)).toBe(STUB_DEFAULT_ENGINE_KEY);
  });

  it("folds undefined to the default slug", () => {
    expect(repoSectionKey(undefined, STUB_DEFAULT_ENGINE_KEY)).toBe(STUB_DEFAULT_ENGINE_KEY);
  });

  it("folds the default slug itself to the default slug", () => {
    expect(repoSectionKey(STUB_DEFAULT_ENGINE_KEY, STUB_DEFAULT_ENGINE_KEY)).toBe(STUB_DEFAULT_ENGINE_KEY);
  });

  it("passes a non-default engine key through unchanged", () => {
    expect(repoSectionKey("acme/sprocket", STUB_DEFAULT_ENGINE_KEY)).toBe("acme/sprocket");
  });
});

describe("titleCaseRepoName", () => {
  it("title-cases a simple repo name from a slug", () => {
    expect(titleCaseRepoName("DejvMatikar/alan")).toBe("Alan");
  });

  it("title-cases each hyphen/underscore-separated word", () => {
    expect(titleCaseRepoName("acme/sprocket-bot_v2")).toBe("Sprocket Bot V2");
  });

  it("handles a bare repo name with no owner prefix", () => {
    expect(titleCaseRepoName("standalone")).toBe("Standalone");
  });
});

describe("registeredRepoEntries", () => {
  it("always includes the default repo even with no engines registered", () => {
    const entries = registeredRepoEntries([], STUB_DEFAULT_ENGINE_KEY);
    expect(entries).toEqual([{ key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: null }]);
  });

  it("dedupes the default engine entry against the seeded default", () => {
    const entries = registeredRepoEntries([engine("DejvMatikar", "alan")], STUB_DEFAULT_ENGINE_KEY);
    expect(entries).toHaveLength(1);
  });

  it("sorts the default repo first, then non-default repos alphabetically by title", () => {
    const entries = registeredRepoEntries([engine("acme", "zeta"), engine("acme", "beta")], STUB_DEFAULT_ENGINE_KEY);
    expect(entries.map((e) => e.title)).toEqual(["Alan", "Beta", "Zeta"]);
  });

  it("carries a real DejvMatikar/alan engine's posture into the default section instead of discarding it behind the synthetic seed", () => {
    const entries = registeredRepoEntries([engine("DejvMatikar", "alan", "propose")], STUB_DEFAULT_ENGINE_KEY);
    const defaultEntry = entries.find((e) => e.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultEntry?.posture).toBe("propose");
  });

  it("threads a non-default engine's posture through unchanged", () => {
    const entries = registeredRepoEntries([engine("acme", "sprocket", "autonomous")], STUB_DEFAULT_ENGINE_KEY);
    const sprocketEntry = entries.find((e) => e.key === "acme/sprocket");
    expect(sprocketEntry?.posture).toBe("autonomous");
  });

  it("dedupes two engines that fold to the same key, keeping the first", () => {
    const entries = registeredRepoEntries(
      [engine("acme", "sprocket", "propose"), engine("acme", "sprocket", "autonomous")],
      STUB_DEFAULT_ENGINE_KEY,
    );
    const sprocketEntries = entries.filter((e) => e.key === "acme/sprocket");
    expect(sprocketEntries).toHaveLength(1);
    expect(sprocketEntries[0]?.posture).toBe("propose");
  });
});

describe("switcherEntries", () => {
  it("returns an empty list for zero engines", () => {
    expect(switcherEntries([], STUB_DEFAULT_ENGINE_KEY)).toEqual([]);
  });

  it("returns an empty list when only an unconfigured default (no forge target) is registered", () => {
    expect(switcherEntries([engine("DejvMatikar", "alan")], STUB_DEFAULT_ENGINE_KEY)).toEqual([]);
  });

  it("returns entries for the default engine plus a connected repo", () => {
    const entries = switcherEntries([engine("DejvMatikar", "alan"), engine("acme", "widget")], STUB_DEFAULT_ENGINE_KEY);
    expect(entries.map((e) => e.key)).toEqual([STUB_DEFAULT_ENGINE_KEY, "acme/widget"]);
    expect(entries.map((e) => e.title)).toEqual(["Alan", "Widget"]);
  });

  it("dedupes an engine repeated in the input, keeping the first posture", () => {
    const entries = switcherEntries(
      [engine("acme", "widget", "propose"), engine("acme", "widget", "autonomous")],
      STUB_DEFAULT_ENGINE_KEY,
    );
    expect(entries).toHaveLength(1);
    expect(entries[0]?.posture).toBe("propose");
  });

  it("renders owner/name for two visible repos that share a short name", () => {
    const entries = switcherEntries([engine("acme", "widget"), engine("other-co", "widget")], STUB_DEFAULT_ENGINE_KEY);
    const titles = entries.map((e) => e.title).sort();
    expect(titles).toEqual(["acme/widget", "other-co/widget"]);
  });

  it("a survivor reverts to its short name once the colliding repo is no longer present", () => {
    const before = switcherEntries([engine("acme", "widget"), engine("other-co", "widget")], STUB_DEFAULT_ENGINE_KEY);
    expect(before.find((e) => e.key === "acme/widget")?.title).toBe("acme/widget");

    const after = switcherEntries([engine("acme", "widget")], STUB_DEFAULT_ENGINE_KEY);
    expect(after.find((e) => e.key === "acme/widget")?.title).toBe("Widget");
  });

  it("a hidden engine never appears — the caller is expected to pass only visible engines", () => {
    const entries = switcherEntries([engine("acme", "widget")], STUB_DEFAULT_ENGINE_KEY);
    expect(entries.map((e) => e.key)).toEqual(["acme/widget"]);
  });

  it("sorts the default entry first, then non-default entries alphabetically by title", () => {
    const entries = switcherEntries(
      [engine("DejvMatikar", "alan"), engine("acme", "zeta"), engine("acme", "beta")],
      STUB_DEFAULT_ENGINE_KEY,
    );
    expect(entries.map((e) => e.title)).toEqual(["Alan", "Beta", "Zeta"]);
  });
});

describe("compareRepoEntries", () => {
  const defaultEntry = { key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: null };
  const other = { key: "acme/sprocket", title: "Sprocket", posture: "autonomous" as const };

  it("orders the default entry before any other entry regardless of position", () => {
    expect(compareRepoEntries(STUB_DEFAULT_ENGINE_KEY)(defaultEntry, other)).toBeLessThan(0);
  });

  it("orders any other entry after the default entry regardless of position", () => {
    expect(compareRepoEntries(STUB_DEFAULT_ENGINE_KEY)(other, defaultEntry)).toBeGreaterThan(0);
  });

  it("falls back to title locale comparison between two non-default entries", () => {
    const a = { key: "acme/alpha", title: "Alpha", posture: null };
    const b = { key: "acme/beta", title: "Beta", posture: null };
    expect(compareRepoEntries(STUB_DEFAULT_ENGINE_KEY)(a, b)).toBeLessThan(0);
  });

  it("is reflexive: comparing an entry to itself returns 0", () => {
    expect(compareRepoEntries(STUB_DEFAULT_ENGINE_KEY)(defaultEntry, defaultEntry)).toBe(0);
    expect(compareRepoEntries(STUB_DEFAULT_ENGINE_KEY)(other, other)).toBe(0);
  });
});

describe("buildRepoSections", () => {
  const engines = [engine("DejvMatikar", "alan"), engine("acme", "sprocket")];

  it("surfaces the real DejvMatikar/alan engine's posture on the default section, not null", () => {
    const sections = buildRepoSections(
      [engine("DejvMatikar", "alan", "self_merge"), engine("acme", "sprocket", "propose")],
      [],
      [],
      true,
      STUB_DEFAULT_ENGINE_KEY,
    );
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    const sprocketSection = sections.find((s) => s.key === "acme/sprocket");
    expect(defaultSection?.posture).toBe("self_merge");
    expect(sprocketSection?.posture).toBe("propose");
  });

  it("groups projects and workspaces by their folded repo key", () => {
    const projects = [
      project({
        id: "p-alan",
        title: "Alan Proj",
        engine_key: STUB_DEFAULT_ENGINE_KEY,
        total_spend_usd: 1.5,
      }),
      project({ id: "p-sprocket", title: "Sprocket Proj", engine_key: "acme/sprocket", total_spend_usd: 2 }),
    ];
    const workspaces = [
      ws({ task_id: "w1", project_id: "p-alan", engine_key: null }),
      ws({ task_id: "w2", project_id: "p-sprocket", engine_key: "acme/sprocket" }),
    ];
    const sections = buildRepoSections(engines, projects, workspaces, true, STUB_DEFAULT_ENGINE_KEY);
    expect(sections).toHaveLength(2);
    const alanSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    const sprocketSection = sections.find((s) => s.key === "acme/sprocket");
    expect(alanSection?.projectGroups[0]?.project.id).toBe("p-alan");
    expect(alanSection?.projectGroups[0]?.workspaces.map((w) => w.task_id)).toEqual(["w1"]);
    expect(sprocketSection?.projectGroups[0]?.workspaces.map((w) => w.task_id)).toEqual(["w2"]);
  });

  it("groups a second workspace under the same already-seen project", () => {
    const projects = [project({ id: "p-multi", engine_key: STUB_DEFAULT_ENGINE_KEY })];
    const workspaces = [
      ws({ task_id: "w1", project_id: "p-multi", engine_key: null }),
      ws({ task_id: "w2", project_id: "p-multi", engine_key: null }),
    ];
    const sections = buildRepoSections(engines, projects, workspaces, true, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.projectGroups[0]?.workspaces.map((w) => w.task_id)).toEqual(["w1", "w2"]);
  });

  it("places project-less workspaces into the unassigned bucket of their own repo", () => {
    const workspaces = [ws({ task_id: "solo", project_id: null, engine_key: "acme/sprocket" })];
    const sections = buildRepoSections(engines, [], workspaces, true, STUB_DEFAULT_ENGINE_KEY);
    const sprocketSection = sections.find((s) => s.key === "acme/sprocket");
    expect(sprocketSection?.unassigned.map((w) => w.task_id)).toEqual(["solo"]);
  });

  it("folds a workspace with engine_key matching the default slug into the default section", () => {
    const workspaces = [ws({ task_id: "explicit-default", project_id: null, engine_key: STUB_DEFAULT_ENGINE_KEY })];
    const sections = buildRepoSections(engines, [], workspaces, true, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.unassigned.map((w) => w.task_id)).toEqual(["explicit-default"]);
  });

  it("counts shipped and archived projects per repo", () => {
    const projects = [
      project({ id: "p1", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "completed" }),
      project({ id: "p2", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "archived" }),
      project({ id: "p3", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "active" }),
    ];
    const sections = buildRepoSections(engines, projects, [], true, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.shippedCount).toBe(1);
    expect(defaultSection?.archivedCount).toBe(1);
  });

  it("excludes completed and archived projects from projectGroups while still counting them", () => {
    const projects = [
      project({ id: "p1", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "completed" }),
      project({ id: "p2", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "archived" }),
      project({ id: "p3", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "active" }),
      project({ id: "p4", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "provisioning" }),
    ];
    const sections = buildRepoSections(engines, projects, [], true, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.projectGroups.map((g) => g.project.id)).toEqual(["p3", "p4"]);
    expect(defaultSection?.shippedCount).toBe(1);
    expect(defaultSection?.archivedCount).toBe(1);
  });

  it("re-homes a live workspace whose project is completed into the unassigned bucket instead of dropping it", () => {
    const projects = [project({ id: "p-done", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "completed" })];
    const workspaces = [
      ws({ task_id: "orphan-done", project_id: "p-done", engine_key: null, status: "active", total_cost_usd: 1 }),
    ];
    const sections = buildRepoSections(engines, projects, workspaces, true, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.projectGroups).toHaveLength(0);
    expect(defaultSection?.unassigned.map((w) => w.task_id)).toEqual(["orphan-done"]);
  });

  it("re-homes a live workspace whose project is archived into the unassigned bucket instead of dropping it", () => {
    const projects = [project({ id: "p-arch", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "archived" })];
    const workspaces = [
      ws({ task_id: "orphan-arch", project_id: "p-arch", engine_key: null, status: "awaiting_input" }),
    ];
    const sections = buildRepoSections(engines, projects, workspaces, true, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.projectGroups).toHaveLength(0);
    expect(defaultSection?.unassigned.map((w) => w.task_id)).toEqual(["orphan-arch"]);
  });

  it("yields an empty projectGroups/unassigned pair for a repo with no data", () => {
    const sections = buildRepoSections(engines, [], [], true, STUB_DEFAULT_ENGINE_KEY);
    const sprocketSection = sections.find((s) => s.key === "acme/sprocket");
    expect(sprocketSection).toBeDefined();
    expect(isRepoSectionEmpty(sprocketSection!)).toBe(true);
  });

  it("a repo section with at least one project or unassigned workspace is not empty", () => {
    const sections = buildRepoSections(
      engines, [project({ id: "p1", engine_key: STUB_DEFAULT_ENGINE_KEY })], [], true, STUB_DEFAULT_ENGINE_KEY,
    );
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(isRepoSectionEmpty(defaultSection!)).toBe(false);
  });
});

describe("buildRepoSections — hidden-repo omission", () => {
  const engines = [engine("DejvMatikar", "alan")];

  it("drops a project explicitly bound to a repo key absent from `engines` (a hidden repo) — it appears in no section", () => {
    const projects = [project({ id: "p-hidden", engine_key: "acme/hidden", status: "active" })];
    const sections = buildRepoSections(engines, projects, [], true, STUB_DEFAULT_ENGINE_KEY);
    const allProjectIds = sections.flatMap((s) => s.projectGroups.map((g) => g.project.id));
    expect(allProjectIds).not.toContain("p-hidden");
  });

  it("drops an unassigned workspace explicitly bound to a repo key absent from `engines`", () => {
    const workspaces = [ws({ task_id: "orphan", project_id: null, engine_key: "acme/hidden" })];
    const sections = buildRepoSections(engines, [], workspaces, true, STUB_DEFAULT_ENGINE_KEY);
    const allWorkspaceIds = sections.flatMap((s) => s.unassigned.map((w) => w.task_id));
    expect(allWorkspaceIds).not.toContain("orphan");
  });

  it("drops the same orphaned workspace with showProjects off too", () => {
    const workspaces = [ws({ task_id: "orphan-flat", project_id: null, engine_key: "acme/hidden" })];
    const sections = buildRepoSections(engines, [], workspaces, false, STUB_DEFAULT_ENGINE_KEY);
    const allWorkspaceIds = sections.flatMap((s) => s.unassigned.map((w) => w.task_id));
    expect(allWorkspaceIds).not.toContain("orphan-flat");
  });
});

describe("buildRepoSections — showProjects off", () => {
  const engines = [engine("DejvMatikar", "alan"), engine("acme", "sprocket")];

  it("emits an empty projectGroups regardless of live projects", () => {
    const projects = [project({ id: "p1", engine_key: null, status: "active" })];
    const sections = buildRepoSections(engines, projects, [], false, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.projectGroups).toEqual([]);
  });

  it("keeps a project-bound workspace visible in the flat unassigned list instead of dropping it", () => {
    const projects = [project({ id: "p1", engine_key: null, status: "active" })];
    const workspaces = [ws({ task_id: "w1", project_id: "p1", engine_key: null })];
    const sections = buildRepoSections(engines, projects, workspaces, false, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.unassigned.map((w) => w.task_id)).toEqual(["w1"]);
  });

  it("keeps a project-less workspace visible too, alongside the project-bound one", () => {
    const projects = [project({ id: "p1", engine_key: null, status: "active" })];
    const workspaces = [
      ws({ task_id: "w1", project_id: "p1", engine_key: null }),
      ws({ task_id: "w2", project_id: null, engine_key: null }),
    ];
    const sections = buildRepoSections(engines, projects, workspaces, false, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.unassigned.map((w) => w.task_id).sort()).toEqual(["w1", "w2"]);
  });

  it("still surfaces a workspace whose project is completed or archived — the live/terminal distinction is moot with projects hidden", () => {
    const projects = [project({ id: "p-done", engine_key: null, status: "completed" })];
    const workspaces = [ws({ task_id: "orphan-done", project_id: "p-done", engine_key: null })];
    const sections = buildRepoSections(engines, projects, workspaces, false, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.unassigned.map((w) => w.task_id)).toEqual(["orphan-done"]);
  });

  it("still counts shipped/archived — the caller decides whether to render the footer that reads them", () => {
    const projects = [
      project({ id: "p1", engine_key: null, status: "completed" }),
      project({ id: "p2", engine_key: null, status: "archived" }),
    ];
    const sections = buildRepoSections(engines, projects, [], false, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(defaultSection?.shippedCount).toBe(1);
    expect(defaultSection?.archivedCount).toBe(1);
  });

  it("a repo with no workspaces at all is still empty, even with hidden projects present", () => {
    const projects = [project({ id: "p1", engine_key: null, status: "active" })];
    const sections = buildRepoSections(engines, projects, [], false, STUB_DEFAULT_ENGINE_KEY);
    const defaultSection = sections.find((s) => s.key === STUB_DEFAULT_ENGINE_KEY);
    expect(isRepoSectionEmpty(defaultSection!)).toBe(true);
  });

  it("produces byte-identical output to the pre-change fold — a golden fixture pinning that intended_engine_key has zero effect while the toggle is off", () => {
    const projects = [
      project({
        id: "p1", engine_key: null, intended_engine_key: "acme/sprocket", status: "active",
      }),
      project({ id: "p2", engine_key: "acme/sprocket", status: "completed" }),
    ];
    const workspaces = [
      ws({ task_id: "w1", project_id: "p1", engine_key: null }),
      ws({ task_id: "w2", project_id: null, engine_key: "acme/sprocket" }),
    ];

    const sections = buildRepoSections(engines, projects, workspaces, false, STUB_DEFAULT_ENGINE_KEY);

    const golden: RepoSectionModel[] = [
      {
        key: STUB_DEFAULT_ENGINE_KEY,
        title: "Alan",
        posture: "autonomous",
        projectGroups: [],
        unassigned: [workspaces[0]!],
        shippedCount: 0,
        archivedCount: 0,
      },
      {
        key: "acme/sprocket",
        title: "Sprocket",
        posture: "autonomous",
        projectGroups: [],
        unassigned: [workspaces[1]!],
        shippedCount: 1,
        archivedCount: 0,
      },
    ];
    expect(sections).toEqual(golden);
    expect(buildUnplacedProjectSection(projects, liveProjectIdSet(projects), false, new Set())).toBeNull();
  });
});

describe("projectPlacementKey — named populations", () => {
  const sectionKeys = new Set([STUB_DEFAULT_ENGINE_KEY, "acme/sprocket"]);

  it("bound: a project's own engine_key wins, even over a conflicting intended_engine_key", () => {
    const p = project({ id: "p-bound", engine_key: "acme/sprocket", intended_engine_key: "other/repo" });
    expect(projectPlacementKey(p, sectionKeys)).toBe("acme/sprocket");
  });

  it("unbound + provenance: places under the intended repository when that section exists", () => {
    const p = project({ id: "p-intent", engine_key: null, intended_engine_key: "acme/sprocket" });
    expect(projectPlacementKey(p, sectionKeys)).toBe("acme/sprocket");
  });

  it("unbound + no provenance: falls to the unplaced sentinel", () => {
    const p = project({ id: "p-none", engine_key: null, intended_engine_key: null });
    expect(projectPlacementKey(p, sectionKeys)).toBe(UNPLACED_SECTION_KEY);
  });

  it("unbound + unregistered provenance: falls to the unplaced sentinel, mirroring the backend's degrade-on-unregistered-slug rule", () => {
    const p = project({ id: "p-ghost", engine_key: null, intended_engine_key: "ghost-co/ghost-repo" });
    expect(projectPlacementKey(p, sectionKeys)).toBe(UNPLACED_SECTION_KEY);
  });

  it("pre-existing projects with intended_engine_key absent entirely (not just null) fall to the unplaced sentinel", () => {
    const legacy = project({ id: "p-legacy", engine_key: null });
    delete (legacy as Partial<ProjectListEntry>).intended_engine_key;
    expect(projectPlacementKey(legacy, sectionKeys)).toBe(UNPLACED_SECTION_KEY);
  });

  it("the unplaced section's key and title never reuse the bare word already meaning the within-section workspace bucket", () => {
    expect(UNPLACED_SECTION_KEY).not.toBe("unassigned");
    expect(UNPLACED_SECTION_TITLE).not.toBe("Unassigned");
  });
});

describe("buildUnplacedProjectSection", () => {
  it("returns null when showProjects is false", () => {
    const projects = [project({ id: "p1", engine_key: null })];
    expect(buildUnplacedProjectSection(projects, liveProjectIdSet(projects), false, new Set())).toBeNull();
  });

  it("groups live provenance-less unbound projects under the unplaced sentinel, leaving a bound project out", () => {
    const projects = [
      project({ id: "p-none", engine_key: null, intended_engine_key: null, status: "active" }),
      project({ id: "p-bound", engine_key: STUB_DEFAULT_ENGINE_KEY, status: "active" }),
    ];
    const section = buildUnplacedProjectSection(
      projects, liveProjectIdSet(projects), true, new Set([STUB_DEFAULT_ENGINE_KEY]),
    );
    expect(section?.key).toBe(UNPLACED_SECTION_KEY);
    expect(section?.title).toBe(UNPLACED_SECTION_TITLE);
    expect(section?.projectGroups.map((g) => g.project.id)).toEqual(["p-none"]);
  });

  it("also groups an unbound project whose intended_engine_key names no rendered section", () => {
    const projects = [
      project({ id: "p-ghost", engine_key: null, intended_engine_key: "ghost-co/ghost-repo", status: "active" }),
    ];
    const section = buildUnplacedProjectSection(
      projects, liveProjectIdSet(projects), true, new Set([STUB_DEFAULT_ENGINE_KEY]),
    );
    expect(section?.projectGroups.map((g) => g.project.id)).toEqual(["p-ghost"]);
  });

  it("counts shipped/archived over its own population", () => {
    const projects = [
      project({ id: "p1", engine_key: null, status: "completed" }),
      project({ id: "p2", engine_key: null, status: "archived" }),
    ];
    const section = buildUnplacedProjectSection(projects, liveProjectIdSet(projects), true, new Set());
    expect(section?.shippedCount).toBe(1);
    expect(section?.archivedCount).toBe(1);
  });

  it("excludes a terminal unplaced project from projectGroups while still counting it", () => {
    const projects = [project({ id: "p-done", engine_key: null, status: "completed" })];
    const section = buildUnplacedProjectSection(projects, liveProjectIdSet(projects), true, new Set());
    expect(section?.projectGroups).toHaveLength(0);
    expect(section?.shippedCount).toBe(1);
  });

  it("posture is always null and unassigned is always empty — this section carries no workspace bucket", () => {
    const projects = [project({ id: "p1", engine_key: null })];
    const section = buildUnplacedProjectSection(projects, liveProjectIdSet(projects), true, new Set());
    expect(section?.posture).toBeNull();
    expect(section?.unassigned).toEqual([]);
  });

  it("placement is exclusive — a project never appears in both a named section and the unplaced one", () => {
    const engines = [engine("DejvMatikar", "alan")];
    const projects = [
      project({ id: "p-none", engine_key: null, intended_engine_key: null, status: "active" }),
    ];
    const sections = buildRepoSections(engines, projects, [], true, STUB_DEFAULT_ENGINE_KEY);
    const unplaced = buildUnplacedProjectSection(
      projects,
      liveProjectIdSet(projects),
      true,
      new Set(sections.map((s) => s.key)),
    );
    const namedIds = sections.flatMap((s) => s.projectGroups.map((g) => g.project.id));
    const unplacedIds = unplaced?.projectGroups.map((g) => g.project.id) ?? [];
    expect(namedIds).toEqual([]);
    expect(unplacedIds).toEqual(["p-none"]);
  });
});

describe("sumActiveSeconds", () => {
  it("returns null for an empty list", () => {
    expect(sumActiveSeconds([])).toBeNull();
  });

  it("returns null when every workspace has no recorded active_seconds", () => {
    const workspaces = [
      ws({ task_id: "w1", active_seconds: null }),
      ws({ task_id: "w2" }),
    ];
    expect(sumActiveSeconds(workspaces)).toBeNull();
  });

  it("sums only the workspaces that carry a recorded value, skipping the rest", () => {
    const workspaces = [
      ws({ task_id: "w1", active_seconds: 100 }),
      ws({ task_id: "w2", active_seconds: null }),
      ws({ task_id: "w3", active_seconds: 50 }),
    ];
    expect(sumActiveSeconds(workspaces)).toBe(150);
  });
});

describe("engineKeyMatchesRepo", () => {
  it("matches a null engine_key against the default repo key", () => {
    expect(engineKeyMatchesRepo(null, STUB_DEFAULT_ENGINE_KEY, STUB_DEFAULT_ENGINE_KEY)).toBe(true);
  });

  it("matches the literal default slug against the default repo key", () => {
    expect(engineKeyMatchesRepo(STUB_DEFAULT_ENGINE_KEY, STUB_DEFAULT_ENGINE_KEY, STUB_DEFAULT_ENGINE_KEY)).toBe(true);
  });

  it("matches a non-default engine_key against its own repo key", () => {
    expect(engineKeyMatchesRepo("acme/sprocket", "acme/sprocket", STUB_DEFAULT_ENGINE_KEY)).toBe(true);
  });

  it("does not match a null engine_key against a non-default repo key", () => {
    expect(engineKeyMatchesRepo(null, "acme/sprocket", STUB_DEFAULT_ENGINE_KEY)).toBe(false);
  });

  it("does not match two different non-default engine keys", () => {
    expect(engineKeyMatchesRepo("acme/sprocket", "acme/other", STUB_DEFAULT_ENGINE_KEY)).toBe(false);
  });
});

describe("repoHeaderModel", () => {
  it("returns the registered entry for a known repo key, including its posture", () => {
    const model = repoHeaderModel([engine("acme", "sprocket", "propose")], "acme/sprocket", STUB_DEFAULT_ENGINE_KEY);
    expect(model).toEqual({ key: "acme/sprocket", title: "Sprocket", posture: "propose" });
  });

  it("returns the default repo's entry, including a real posture, for the default key", () => {
    const model = repoHeaderModel([engine("DejvMatikar", "alan", "self_merge")], STUB_DEFAULT_ENGINE_KEY, STUB_DEFAULT_ENGINE_KEY);
    expect(model).toEqual({ key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: "self_merge" });
  });

  it("falls back to a derived title with a null posture for a repo key absent from engines", () => {
    const model = repoHeaderModel([], "acme/ghost", STUB_DEFAULT_ENGINE_KEY);
    expect(model).toEqual({ key: "acme/ghost", title: "Ghost", posture: null });
  });
});
