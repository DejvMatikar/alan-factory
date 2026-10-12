import { describe, expect, it } from "vitest";

import type { ProjectListEntry } from "../api";
import {
  DEFAULT_KEY_STATES,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
  type DefaultKeyState,
} from "../test/engineFixtures";
import { makeProjectListEntry } from "../test/projectFixtures";
import { makeWorkspaceSummary } from "../test/workspaceFixtures";
import {
  buildRepoSections,
  buildUnplacedProjectSection,
  compareRepoEntries,
  engineKeyMatchesRepo,
  liveProjectIdSet,
  projectPlacementKey,
  registeredRepoEntries,
  repoHeaderModel,
  repoSectionKey,
  switcherEntries,
  UNPLACED_SECTION_KEY,
} from "./fold";

const SPROCKET_KEY = "acme/sprocket";
const AARDVARK_KEY = "acme/aardvark";
const ZETA_KEY = "acme/zeta";

const DEFAULT_TITLE: Readonly<Record<DefaultKeyState, string>> = {
  equal: "Alan",
  other: "Listed Default",
};

function keysOf(entries: readonly { key: string }[]): string[] {
  return entries.map((entry) => entry.key);
}

function byKey<T extends { key: string }>(sections: readonly T[], key: string): T {
  const section = sections.find((candidate) => candidate.key === key);
  if (section === undefined) throw new Error(`nothing keyed ${key}`);
  return section;
}

describe("repoSectionKey — resolved default", () => {
  it.each(DEFAULT_KEY_STATES)("attributes a null or undefined engine key to the default (%s)", (_state, listed) => {
    const defaultKey = listed;
    expect(repoSectionKey(null, defaultKey)).toBe(defaultKey);
    expect(repoSectionKey(undefined, defaultKey)).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("passes an explicit engine key through unchanged (%s)", (_state, listed) => {
    expect(repoSectionKey(SPROCKET_KEY, listed)).toBe(SPROCKET_KEY);
  });

  it("treats the old built-in value as an ordinary key once the listed default differs", () => {
    expect(repoSectionKey(STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY)).toBe(STUB_DEFAULT_ENGINE_KEY);
    expect(repoSectionKey(null, LISTED_DEFAULT_KEY)).toBe(LISTED_DEFAULT_KEY);
  });
});

describe("engineKeyMatchesRepo — resolved default", () => {
  it.each(DEFAULT_KEY_STATES)("matches an item carrying no repository against the default repo key (%s)", (_state, listed) => {
    const defaultKey = listed;
    expect(engineKeyMatchesRepo(null, defaultKey, defaultKey)).toBe(true);
    expect(engineKeyMatchesRepo(undefined, defaultKey, defaultKey)).toBe(true);
  });

  it.each(DEFAULT_KEY_STATES)("matches the literal default key against the default repo key (%s)", (_state, listed) => {
    const defaultKey = listed;
    expect(engineKeyMatchesRepo(defaultKey, defaultKey, defaultKey)).toBe(true);
  });

  it.each(DEFAULT_KEY_STATES)("does not match an item carrying no repository against another repo key (%s)", (_state, listed) => {
    expect(engineKeyMatchesRepo(null, SPROCKET_KEY, listed)).toBe(false);
  });

  it("matches the old built-in value only against its own key once the listed default differs", () => {
    expect(engineKeyMatchesRepo(STUB_DEFAULT_ENGINE_KEY, STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY)).toBe(true);
    expect(engineKeyMatchesRepo(STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY, LISTED_DEFAULT_KEY)).toBe(false);
    expect(engineKeyMatchesRepo(null, STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY)).toBe(false);
  });
});

describe("registeredRepoEntries — resolved default", () => {
  it.each(DEFAULT_KEY_STATES)("seeds the default repository when no engine is registered for it (%s)", (state, listed) => {
    const defaultKey = listed;
    expect(registeredRepoEntries([], defaultKey)).toEqual([
      { key: defaultKey, title: DEFAULT_TITLE[state], posture: null },
    ]);
  });

  it.each(DEFAULT_KEY_STATES)("carries the default engine's posture without duplicating the entry (%s)", (state, listed) => {
    const defaultKey = listed;
    const entries = registeredRepoEntries([makeEngineSummaryFor(defaultKey, { posture: "propose" })], defaultKey);
    expect(entries).toEqual([{ key: defaultKey, title: DEFAULT_TITLE[state], posture: "propose" }]);
  });

  it.each(DEFAULT_KEY_STATES)("sorts the default first even ahead of an alphabetically earlier repository (%s)", (_state, listed) => {
    const defaultKey = listed;
    const entries = registeredRepoEntries(
      [makeEngineSummaryFor(ZETA_KEY), makeEngineSummaryFor(AARDVARK_KEY)],
      defaultKey,
    );
    expect(keysOf(entries)).toEqual([defaultKey, AARDVARK_KEY, ZETA_KEY]);
  });

  it("lists the old built-in value as an ordinary repository, alphabetical rather than first, when an engine carries it", () => {
    const entries = registeredRepoEntries(
      [
        makeEngineSummaryFor(ZETA_KEY),
        makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY, { posture: "propose" }),
        makeEngineSummaryFor(AARDVARK_KEY),
      ],
      LISTED_DEFAULT_KEY,
    );
    expect(keysOf(entries)).toEqual([LISTED_DEFAULT_KEY, AARDVARK_KEY, STUB_DEFAULT_ENGINE_KEY, ZETA_KEY]);
    expect(byKey(entries, STUB_DEFAULT_ENGINE_KEY).posture).toBe("propose");
  });

  it("gives the old built-in value no entry when no engine carries it", () => {
    const entries = registeredRepoEntries([makeEngineSummaryFor(SPROCKET_KEY)], LISTED_DEFAULT_KEY);
    expect(keysOf(entries)).toEqual([LISTED_DEFAULT_KEY, SPROCKET_KEY]);
  });
});

describe("switcherEntries — resolved default", () => {
  it.each(DEFAULT_KEY_STATES)("returns an empty list when the only engine is an unconfigured default (%s)", (_state, listed) => {
    const defaultKey = listed;
    expect(switcherEntries([makeEngineSummaryFor(defaultKey)], defaultKey)).toEqual([]);
  });

  it.each(DEFAULT_KEY_STATES)("lists the default first with its own posture, then the others alphabetically (%s)", (state, listed) => {
    const defaultKey = listed;
    const entries = switcherEntries(
      [
        makeEngineSummaryFor(ZETA_KEY),
        makeEngineSummaryFor(defaultKey, { posture: "propose" }),
        makeEngineSummaryFor(AARDVARK_KEY),
      ],
      defaultKey,
    );
    expect(entries.map((entry) => entry.title)).toEqual([DEFAULT_TITLE[state], "Aardvark", "Zeta"]);
    expect(keysOf(entries)).toEqual([defaultKey, AARDVARK_KEY, ZETA_KEY]);
    expect(entries[0]?.posture).toBe("propose");
  });

  it("lists the old built-in value as an ordinary repository when it is the only engine under a different listed default", () => {
    const entries = switcherEntries(
      [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY, { posture: "propose" })],
      LISTED_DEFAULT_KEY,
    );
    expect(entries).toEqual([{ key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: "propose" }]);
  });

  it("sorts the old built-in value alphabetically after the listed default", () => {
    const entries = switcherEntries(
      [
        makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY),
        makeEngineSummaryFor(LISTED_DEFAULT_KEY),
        makeEngineSummaryFor(AARDVARK_KEY),
      ],
      LISTED_DEFAULT_KEY,
    );
    expect(keysOf(entries)).toEqual([LISTED_DEFAULT_KEY, AARDVARK_KEY, STUB_DEFAULT_ENGINE_KEY]);
  });
});

describe("repoHeaderModel — resolved default", () => {
  it.each(DEFAULT_KEY_STATES)("returns the default repository's entry with its engine's posture (%s)", (state, listed) => {
    const defaultKey = listed;
    const model = repoHeaderModel([makeEngineSummaryFor(defaultKey, { posture: "self_merge" })], defaultKey, defaultKey);
    expect(model).toEqual({ key: defaultKey, title: DEFAULT_TITLE[state], posture: "self_merge" });
  });

  it.each(DEFAULT_KEY_STATES)("returns the seeded default entry with a null posture when no engine carries it (%s)", (state, listed) => {
    const defaultKey = listed;
    expect(repoHeaderModel([], defaultKey, defaultKey)).toEqual({
      key: defaultKey,
      title: DEFAULT_TITLE[state],
      posture: null,
    });
  });

  it.each(DEFAULT_KEY_STATES)("falls back to a derived title with a null posture for a repo key absent from engines (%s)", (_state, listed) => {
    expect(repoHeaderModel([], "acme/ghost", listed)).toEqual({
      key: "acme/ghost",
      title: "Ghost",
      posture: null,
    });
  });

  it("returns the old built-in value's own engine posture as an ordinary repository", () => {
    const model = repoHeaderModel(
      [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY, { posture: "propose" })],
      STUB_DEFAULT_ENGINE_KEY,
      LISTED_DEFAULT_KEY,
    );
    expect(model).toEqual({ key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: "propose" });
  });

  it("returns a null posture for the old built-in value when no engine carries it", () => {
    const model = repoHeaderModel([], STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY);
    expect(model).toEqual({ key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: null });
  });
});

describe("projectPlacementKey — resolved default", () => {
  it.each(DEFAULT_KEY_STATES)("bound: a project's own engine key wins over a default provenance (%s)", (_state, listed) => {
    const defaultKey = listed;
    const project = makeProjectListEntry({ engine_key: SPROCKET_KEY, intended_engine_key: defaultKey });
    expect(projectPlacementKey(project, new Set([defaultKey, SPROCKET_KEY]))).toBe(SPROCKET_KEY);
  });

  it.each(DEFAULT_KEY_STATES)("unbound with the default as provenance: places under the default when its section is rendered (%s)", (_state, listed) => {
    const defaultKey = listed;
    const project = makeProjectListEntry({ engine_key: null, intended_engine_key: defaultKey });
    expect(projectPlacementKey(project, new Set([defaultKey]))).toBe(defaultKey);
  });

  it.each(DEFAULT_KEY_STATES)("unbound with the default as provenance: falls to the unplaced sentinel when no section is rendered (%s)", (_state, listed) => {
    const defaultKey = listed;
    const project = makeProjectListEntry({ engine_key: null, intended_engine_key: defaultKey });
    expect(projectPlacementKey(project, new Set())).toBe(UNPLACED_SECTION_KEY);
  });

  it.each(DEFAULT_KEY_STATES)("unbound without provenance falls to the unplaced sentinel, never to the default (%s)", (_state, listed) => {
    const defaultKey = listed;
    const project = makeProjectListEntry({ engine_key: null, intended_engine_key: null });
    expect(projectPlacementKey(project, new Set([defaultKey]))).toBe(UNPLACED_SECTION_KEY);
  });

  it("unbound with the old built-in value as provenance: places under it only when it is a rendered section", () => {
    const project = makeProjectListEntry({ engine_key: null, intended_engine_key: STUB_DEFAULT_ENGINE_KEY });
    expect(projectPlacementKey(project, new Set([LISTED_DEFAULT_KEY]))).toBe(UNPLACED_SECTION_KEY);
    expect(
      projectPlacementKey(project, new Set([LISTED_DEFAULT_KEY, STUB_DEFAULT_ENGINE_KEY])),
    ).toBe(STUB_DEFAULT_ENGINE_KEY);
  });
});

describe("buildRepoSections — resolved default", () => {
  it.each(DEFAULT_KEY_STATES)("emits the default section first and present even with no engine registered for it (%s)", (state, listed) => {
    const defaultKey = listed;
    const sections = buildRepoSections(
      [makeEngineSummaryFor(ZETA_KEY), makeEngineSummaryFor(AARDVARK_KEY)],
      [],
      [],
      true,
      defaultKey,
    );
    expect(keysOf(sections)).toEqual([defaultKey, AARDVARK_KEY, ZETA_KEY]);
    expect(sections[0]).toMatchObject({ title: DEFAULT_TITLE[state], posture: null });
  });

  it.each(DEFAULT_KEY_STATES)("carries the default engine's posture into its section (%s)", (_state, listed) => {
    const defaultKey = listed;
    const sections = buildRepoSections(
      [makeEngineSummaryFor(defaultKey, { posture: "self_merge" }), makeEngineSummaryFor(SPROCKET_KEY, { posture: "propose" })],
      [],
      [],
      true,
      defaultKey,
    );
    expect(byKey(sections, defaultKey).posture).toBe("self_merge");
    expect(byKey(sections, SPROCKET_KEY).posture).toBe("propose");
  });

  it.each(DEFAULT_KEY_STATES)("attributes workspaces carrying no repository to the default section only (%s)", (_state, listed) => {
    const defaultKey = listed;
    const workspaces = [
      makeWorkspaceSummary({ task_id: "w-absent" }),
      makeWorkspaceSummary({ task_id: "w-null", engine_key: null }),
      makeWorkspaceSummary({ task_id: "w-explicit", engine_key: defaultKey }),
      makeWorkspaceSummary({ task_id: "w-sprocket", engine_key: SPROCKET_KEY }),
    ];
    const sections = buildRepoSections([makeEngineSummaryFor(SPROCKET_KEY)], [], workspaces, true, defaultKey);
    expect(byKey(sections, defaultKey).unassigned.map((w) => w.task_id)).toEqual(["w-absent", "w-null", "w-explicit"]);
    expect(byKey(sections, SPROCKET_KEY).unassigned.map((w) => w.task_id)).toEqual(["w-sprocket"]);
  });

  it.each(DEFAULT_KEY_STATES)("groups a project bound to the default, with its workspaces, under the default section (%s)", (_state, listed) => {
    const defaultKey = listed;
    const projects = [makeProjectListEntry({ id: "p-default", engine_key: defaultKey })];
    const workspaces = [makeWorkspaceSummary({ task_id: "w1", project_id: "p-default", engine_key: null })];
    const sections = buildRepoSections([makeEngineSummaryFor(SPROCKET_KEY)], projects, workspaces, true, defaultKey);
    const defaultSection = byKey(sections, defaultKey);
    expect(defaultSection.projectGroups.map((g) => g.project.id)).toEqual(["p-default"]);
    expect(defaultSection.projectGroups[0]?.workspaces.map((w) => w.task_id)).toEqual(["w1"]);
    expect(defaultSection.unassigned).toEqual([]);
  });

  it.each(DEFAULT_KEY_STATES)("places an unbound project whose provenance is the default under the default section (%s)", (_state, listed) => {
    const defaultKey = listed;
    const projects = [makeProjectListEntry({ id: "p-intent", engine_key: null, intended_engine_key: defaultKey })];
    const sections = buildRepoSections([makeEngineSummaryFor(SPROCKET_KEY)], projects, [], true, defaultKey);
    expect(byKey(sections, defaultKey).projectGroups.map((g) => g.project.id)).toEqual(["p-intent"]);
    expect(byKey(sections, SPROCKET_KEY).projectGroups).toEqual([]);
  });

  it.each(DEFAULT_KEY_STATES)("attributes a repository-less project to the default section's counts while projects are hidden (%s)", (_state, listed) => {
    const defaultKey = listed;
    const projects = [
      makeProjectListEntry({ id: "p-done", engine_key: null, status: "completed" }),
      makeProjectListEntry({ id: "p-arch", engine_key: null, status: "archived" }),
    ];
    const sections = buildRepoSections([makeEngineSummaryFor(SPROCKET_KEY)], projects, [], false, defaultKey);
    expect(byKey(sections, defaultKey)).toMatchObject({ shippedCount: 1, archivedCount: 1 });
    expect(byKey(sections, SPROCKET_KEY)).toMatchObject({ shippedCount: 0, archivedCount: 0 });
  });

  it("gives a repository equal to the old built-in value its own section, holding only items that name it, when an engine carries it", () => {
    const workspaces = [
      makeWorkspaceSummary({ task_id: "w-null", engine_key: null }),
      makeWorkspaceSummary({ task_id: "w-builtin", engine_key: STUB_DEFAULT_ENGINE_KEY }),
    ];
    const sections = buildRepoSections(
      [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY), makeEngineSummaryFor(SPROCKET_KEY)],
      [],
      workspaces,
      true,
      LISTED_DEFAULT_KEY,
    );
    expect(keysOf(sections)).toEqual([LISTED_DEFAULT_KEY, STUB_DEFAULT_ENGINE_KEY, SPROCKET_KEY]);
    expect(byKey(sections, LISTED_DEFAULT_KEY).unassigned.map((w) => w.task_id)).toEqual(["w-null"]);
    expect(byKey(sections, STUB_DEFAULT_ENGINE_KEY).unassigned.map((w) => w.task_id)).toEqual(["w-builtin"]);
  });

  it("drops items bound to the old built-in value from every section when no engine carries it", () => {
    const projects = [makeProjectListEntry({ id: "p-builtin", engine_key: STUB_DEFAULT_ENGINE_KEY })];
    const workspaces = [makeWorkspaceSummary({ task_id: "w-builtin", engine_key: STUB_DEFAULT_ENGINE_KEY })];
    const sections = buildRepoSections([makeEngineSummaryFor(SPROCKET_KEY)], projects, workspaces, true, LISTED_DEFAULT_KEY);
    expect(keysOf(sections)).toEqual([LISTED_DEFAULT_KEY, SPROCKET_KEY]);
    expect(sections.flatMap((s) => s.projectGroups)).toEqual([]);
    expect(sections.flatMap((s) => s.unassigned)).toEqual([]);
  });
});

describe("buildUnplacedProjectSection — resolved default", () => {
  function unplacedIds(
    projects: readonly ProjectListEntry[],
    sectionKeys: ReadonlySet<string>,
  ): string[] | undefined {
    const section = buildUnplacedProjectSection(projects, liveProjectIdSet(projects), true, sectionKeys);
    return section?.projectGroups.map((g) => g.project.id);
  }

  it.each(DEFAULT_KEY_STATES)("leaves out projects bound to, or with provenance of, the default once its section is rendered (%s)", (_state, listed) => {
    const defaultKey = listed;
    const projects = [
      makeProjectListEntry({ id: "p-bound", engine_key: defaultKey }),
      makeProjectListEntry({ id: "p-intent", engine_key: null, intended_engine_key: defaultKey }),
      makeProjectListEntry({ id: "p-none", engine_key: null, intended_engine_key: null }),
    ];
    expect(unplacedIds(projects, new Set([defaultKey]))).toEqual(["p-none"]);
  });

  it.each(DEFAULT_KEY_STATES)("includes a project whose provenance is the default when no default section is rendered (%s)", (_state, listed) => {
    const defaultKey = listed;
    const projects = [
      makeProjectListEntry({ id: "p-intent", engine_key: null, intended_engine_key: defaultKey }),
      makeProjectListEntry({ id: "p-none", engine_key: null, intended_engine_key: null }),
    ];
    expect(unplacedIds(projects, new Set())).toEqual(["p-intent", "p-none"]);
  });

  it.each(DEFAULT_KEY_STATES)("is exclusive with the named sections built for the same default (%s)", (_state, listed) => {
    const defaultKey = listed;
    const projects = [
      makeProjectListEntry({ id: "p-intent", engine_key: null, intended_engine_key: defaultKey }),
      makeProjectListEntry({ id: "p-none", engine_key: null, intended_engine_key: null }),
    ];
    const sections = buildRepoSections([makeEngineSummaryFor(SPROCKET_KEY)], projects, [], true, defaultKey);
    const namedIds = sections.flatMap((s) => s.projectGroups.map((g) => g.project.id));
    expect(namedIds).toEqual(["p-intent"]);
    expect(unplacedIds(projects, new Set(keysOf(sections)))).toEqual(["p-none"]);
  });

  it("returns null while projects are hidden, whatever the default", () => {
    const projects = [makeProjectListEntry({ id: "p1", engine_key: null })];
    expect(
      buildUnplacedProjectSection(projects, liveProjectIdSet(projects), false, new Set()),
    ).toBeNull();
  });

  it("holds a project whose provenance is the old built-in value when no engine carries it", () => {
    const projects = [
      makeProjectListEntry({ id: "p-builtin", engine_key: null, intended_engine_key: STUB_DEFAULT_ENGINE_KEY }),
    ];
    expect(unplacedIds(projects, new Set([LISTED_DEFAULT_KEY]))).toEqual(["p-builtin"]);
  });
});

describe("compareRepoEntries — resolved default", () => {
  const sprocket = { key: SPROCKET_KEY, title: "Sprocket", posture: null };
  const aardvark = { key: AARDVARK_KEY, title: "Aardvark", posture: null };
  const zeta = { key: ZETA_KEY, title: "Zeta", posture: null };

  it.each(DEFAULT_KEY_STATES)("orders the default entry before any other regardless of position (%s)", (state, listed) => {
    const defaultKey = listed;
    const defaultEntry = { key: defaultKey, title: DEFAULT_TITLE[state], posture: null };
    const compare = compareRepoEntries(defaultKey);
    expect(compare(defaultEntry, aardvark)).toBeLessThan(0);
    expect(compare(aardvark, defaultEntry)).toBeGreaterThan(0);
  });

  it.each(DEFAULT_KEY_STATES)("falls back to title comparison between two other entries (%s)", (_state, listed) => {
    const compare = compareRepoEntries(listed);
    expect(compare(aardvark, sprocket)).toBeLessThan(0);
    expect(compare(sprocket, aardvark)).toBeGreaterThan(0);
  });

  it.each(DEFAULT_KEY_STATES)("is reflexive for the default entry and for another entry (%s)", (state, listed) => {
    const defaultKey = listed;
    const defaultEntry = { key: defaultKey, title: DEFAULT_TITLE[state], posture: null };
    const compare = compareRepoEntries(defaultKey);
    expect(compare(defaultEntry, defaultEntry)).toBe(0);
    expect(compare(sprocket, sprocket)).toBe(0);
  });

  it.each(DEFAULT_KEY_STATES)("sorts a list with the default first, then alphabetically by title (%s)", (state, listed) => {
    const defaultKey = listed;
    const defaultEntry = { key: defaultKey, title: DEFAULT_TITLE[state], posture: null };
    const sorted = [zeta, defaultEntry, aardvark].sort(compareRepoEntries(defaultKey));
    expect(keysOf(sorted)).toEqual([defaultKey, AARDVARK_KEY, ZETA_KEY]);
  });

  it("orders the old built-in value by title rather than first once the listed default differs", () => {
    const builtIn = { key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: null };
    expect(compareRepoEntries(LISTED_DEFAULT_KEY)(builtIn, aardvark)).toBeGreaterThan(0);
    expect(compareRepoEntries(STUB_DEFAULT_ENGINE_KEY)(builtIn, aardvark)).toBeLessThan(0);
  });

  it("orders the listed default before the old built-in value", () => {
    const builtIn = { key: STUB_DEFAULT_ENGINE_KEY, title: "Alan", posture: null };
    const listedEntry = { key: LISTED_DEFAULT_KEY, title: "Listed Default", posture: null };
    const compare = compareRepoEntries(LISTED_DEFAULT_KEY);
    expect(compare(listedEntry, builtIn)).toBeLessThan(0);
    expect(compare(builtIn, listedEntry)).toBeGreaterThan(0);
  });
});

describe("fold — no resolved default", () => {
  it("attributes an item carrying no repository to no repository", () => {
    expect(repoSectionKey(null, null)).toBeNull();
    expect(repoSectionKey(undefined, null)).toBeNull();
    expect(repoSectionKey(SPROCKET_KEY, null)).toBe(SPROCKET_KEY);
  });

  it("matches an item carrying no repository against no repository key", () => {
    expect(engineKeyMatchesRepo(null, SPROCKET_KEY, null)).toBe(false);
    expect(engineKeyMatchesRepo(undefined, STUB_DEFAULT_ENGINE_KEY, null)).toBe(false);
    expect(engineKeyMatchesRepo(SPROCKET_KEY, SPROCKET_KEY, null)).toBe(true);
  });

  it("seeds no repository when no engine is registered", () => {
    expect(registeredRepoEntries([], null)).toEqual([]);
  });

  it("lists only the registered repositories, alphabetically by title", () => {
    const entries = registeredRepoEntries(
      [makeEngineSummaryFor(ZETA_KEY), makeEngineSummaryFor(AARDVARK_KEY)],
      null,
    );
    expect(keysOf(entries)).toEqual([AARDVARK_KEY, ZETA_KEY]);
  });

  it("lists a lone unconfigured engine in the switcher, since nothing is the default", () => {
    expect(keysOf(switcherEntries([makeEngineSummaryFor(AARDVARK_KEY)], null))).toEqual([AARDVARK_KEY]);
  });

  it("returns an empty switcher list for zero engines", () => {
    expect(switcherEntries([], null)).toEqual([]);
  });

  it("orders every entry by title", () => {
    const compare = compareRepoEntries(null);
    const aardvark = { key: AARDVARK_KEY, title: "Aardvark", posture: null };
    const zeta = { key: ZETA_KEY, title: "Zeta", posture: null };
    expect(compare(aardvark, zeta)).toBeLessThan(0);
    expect(compare(zeta, aardvark)).toBeGreaterThan(0);
    expect(compare(zeta, zeta)).toBe(0);
  });

  it("derives a header from the repo key alone when no engine carries it", () => {
    expect(repoHeaderModel([], "acme/ghost", null)).toEqual({
      key: "acme/ghost",
      title: "Ghost",
      posture: null,
    });
  });

  it("gives workspaces and projects carrying no repository no section", () => {
    const workspaces = [
      makeWorkspaceSummary({ task_id: "w-null", engine_key: null }),
      makeWorkspaceSummary({ task_id: "w-sprocket", engine_key: SPROCKET_KEY }),
    ];
    const projects = [
      makeProjectListEntry({ id: "p-none", engine_key: null, status: "completed" }),
      makeProjectListEntry({ id: "p-sprocket", engine_key: SPROCKET_KEY, status: "completed" }),
    ];
    const sections = buildRepoSections([makeEngineSummaryFor(SPROCKET_KEY)], projects, workspaces, false, null);
    expect(keysOf(sections)).toEqual([SPROCKET_KEY]);
    expect(byKey(sections, SPROCKET_KEY).unassigned.map((w) => w.task_id)).toEqual(["w-sprocket"]);
    expect(byKey(sections, SPROCKET_KEY).shippedCount).toBe(1);
  });
});
