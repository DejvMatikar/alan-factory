import { describe, expect, it } from "vitest";

import type { WorkspaceSummary } from "../../../api";
import {
  POSTURE_UNAVAILABLE_REASON,
  buildPostureRungs,
  hasPrBearingWorkspace,
  isSelectable,
  preflightRegister,
  selectReusableProject,
} from "./connectStep4PostureLogic";
import { makeProjectListEntry } from "../../../test/projectFixtures";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";

function workspace(overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return makeWorkspaceSummary({ status: "completed", team_runs_total: 1, invocations_total: 1, ...overrides });
}

describe("preflightRegister", () => {
  it("reads pass as ready to dispatch", () => {
    expect(preflightRegister("pass")).toBe("ready to dispatch");
  });

  it("reads fail as blocked", () => {
    expect(preflightRegister("fail")).toBe("blocked");
  });

  it("reads unknown as partially ready", () => {
    expect(preflightRegister("unknown")).toBe("partially ready");
  });

  it("reads a never-run (null) result as partially ready, never blocked", () => {
    expect(preflightRegister(null)).toBe("partially ready");
  });
});

describe("hasPrBearingWorkspace", () => {
  const engineKey = "acme/widget";

  it("is false for an empty workspace list", () => {
    expect(hasPrBearingWorkspace([], engineKey)).toBe(false);
  });

  it("is false when no workspace matches the engine key", () => {
    const rows = [workspace({ engine_key: "other/repo", pr_number: 3 })];
    expect(hasPrBearingWorkspace(rows, engineKey)).toBe(false);
  });

  it("is false when every matching workspace has a null or absent pr_number", () => {
    const rows = [
      workspace({ engine_key: engineKey, pr_number: null }),
      workspace({ engine_key: engineKey }),
    ];
    expect(hasPrBearingWorkspace(rows, engineKey)).toBe(false);
  });

  it("is true when one matching workspace carries a pr_number", () => {
    const rows = [
      workspace({ engine_key: "other/repo", pr_number: 9 }),
      workspace({ engine_key: engineKey, pr_number: null }),
      workspace({ engine_key: engineKey, pr_number: 42 }),
    ];
    expect(hasPrBearingWorkspace(rows, engineKey)).toBe(true);
  });
});

describe("selectReusableProject", () => {
  const engineKey = "acme/widget";

  it("returns the project bound to this engine", () => {
    const p = makeProjectListEntry({ id: "p-bound", engine_key: engineKey });
    expect(selectReusableProject([p], engineKey)).toBe(p);
  });

  it("returns an unbound project recording intent toward this engine", () => {
    const p = makeProjectListEntry({ id: "p-intent", engine_key: null, intended_engine_key: engineKey });
    expect(selectReusableProject([p], engineKey)).toBe(p);
  });

  it("prefers a bound match over an unbound intent match when both exist", () => {
    const bound = makeProjectListEntry({ id: "p-bound", engine_key: engineKey });
    const intent = makeProjectListEntry({ id: "p-intent", engine_key: null, intended_engine_key: engineKey });
    expect(selectReusableProject([intent, bound], engineKey)).toBe(bound);
  });

  it("never adopts a project bound to a different engine, even one carrying this intent", () => {
    const otherBound = makeProjectListEntry({
      id: "p-other",
      engine_key: "other/repo",
      intended_engine_key: engineKey,
    });
    const intent = makeProjectListEntry({ id: "p-intent", engine_key: null, intended_engine_key: engineKey });
    expect(selectReusableProject([otherBound, intent], engineKey)).toBe(intent);
  });

  it("returns undefined when nothing matches", () => {
    const p = makeProjectListEntry({ id: "p-other", engine_key: "other/repo" });
    expect(selectReusableProject([p], engineKey)).toBeUndefined();
  });

  it("returns the first of two unbound projects sharing the same intent, in server order", () => {
    const first = makeProjectListEntry({ id: "p-first", engine_key: null, intended_engine_key: engineKey });
    const second = makeProjectListEntry({ id: "p-second", engine_key: null, intended_engine_key: engineKey });
    expect(selectReusableProject([first, second], engineKey)).toBe(first);
  });
});

describe("buildPostureRungs", () => {
  it("greys self_merge and autonomous when there is no merged work", () => {
    const rows = buildPostureRungs(false);
    const byValue = Object.fromEntries(rows.map((r) => [r.value, r.selectable]));
    expect(byValue).toEqual({
      observe: true,
      propose: true,
      self_merge: false,
      autonomous: false,
    });
  });

  it("un-greys self_merge and autonomous once there is merged work", () => {
    const rows = buildPostureRungs(true);
    const byValue = Object.fromEntries(rows.map((r) => [r.value, r.selectable]));
    expect(byValue).toEqual({
      observe: true,
      propose: true,
      self_merge: true,
      autonomous: true,
    });
  });

  it("returns exactly the four postures in rank order", () => {
    const rows = buildPostureRungs(false);
    expect(rows.map((r) => r.value)).toEqual(["observe", "propose", "self_merge", "autonomous"]);
  });

  it("captions match the admitted-teams table verbatim", () => {
    const rows = buildPostureRungs(true);
    const byValue = Object.fromEntries(rows.map((r) => [r.value, r.caption]));
    expect(byValue.observe).toBe(
      "Alan reads and reports on this repo but dispatches no teams at all — no research, no planning, no changes.",
    );
    expect(byValue.propose).toBe(
      "Alan researches, plans, and implements changes for this repo, but every implementation pauses for your approval before it can merge.",
    );
    expect(byValue.self_merge).toBe(
      "Adds verifying and merging passing changes on its own, with no approval step.",
    );
    expect(byValue.autonomous).toBe(
      "Adds fully unattended maintenance — triage, dependency sweeps, and deploy checks — dispatched without you.",
    );
  });
});

describe("isSelectable", () => {
  it("is true for a selectable rung and false for a greyed one", () => {
    const rows = buildPostureRungs(false);
    expect(isSelectable(rows, "propose")).toBe(true);
    expect(isSelectable(rows, "self_merge")).toBe(false);
  });

  it("reflects merged-work state on both sides", () => {
    const withWork = buildPostureRungs(true);
    expect(isSelectable(withWork, "autonomous")).toBe(true);
  });
});

describe("POSTURE_UNAVAILABLE_REASON", () => {
  it("is a non-empty, stable string", () => {
    expect(POSTURE_UNAVAILABLE_REASON.length).toBeGreaterThan(0);
  });
});
