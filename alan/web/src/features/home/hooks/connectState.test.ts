import { describe, expect, it } from "vitest";

import type { WorkspaceSummary } from "../../../api";
import type { WizardStepCompletion } from "../../../lib/wizardStepSelection";
import { makeProjectListEntry } from "../../../test/projectFixtures";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import { hostSetupMessage, isMidSetup, operationalEngineKeys, resumeStepNumber } from "./connectState";

function workspace(overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return makeWorkspaceSummary({ status: "completed", team_runs_total: 1, invocations_total: 1, ...overrides });
}

function completion(overrides: Partial<WizardStepCompletion> = {}): WizardStepCompletion {
  return { identify: true, configure: true, dispatchable: true, posture: false, ...overrides };
}

describe("isMidSetup", () => {
  it("is false once identify and dispatchable are both complete, regardless of configure", () => {
    expect(isMidSetup(completion({ configure: false }))).toBe(false);
    expect(isMidSetup(completion({ configure: true }))).toBe(false);
  });

  it("is false regardless of posture — posture is a choice, not unfinished setup", () => {
    expect(isMidSetup(completion({ posture: false }))).toBe(false);
    expect(isMidSetup(completion({ posture: true }))).toBe(false);
  });

  it("is true when identify is incomplete", () => {
    expect(isMidSetup(completion({ identify: false }))).toBe(true);
  });

  it("is true when dispatchable is incomplete", () => {
    expect(isMidSetup(completion({ dispatchable: false }))).toBe(true);
  });

  it("is true when both identify and dispatchable are incomplete", () => {
    expect(isMidSetup(completion({ identify: false, dispatchable: false }))).toBe(true);
  });
});

describe("resumeStepNumber", () => {
  it("resolves to step 1 when identify is the first incomplete step", () => {
    expect(
      resumeStepNumber(completion({ identify: false, configure: false, dispatchable: false })),
    ).toBe(1);
  });

  it("resolves to step 2 when configure is the first incomplete step", () => {
    expect(
      resumeStepNumber(completion({ identify: true, configure: false, dispatchable: false })),
    ).toBe(2);
  });

  it("resolves to step 3 when dispatchable is the first incomplete step", () => {
    expect(
      resumeStepNumber(completion({ identify: true, configure: true, dispatchable: false })),
    ).toBe(3);
  });

  it("resolves to step 4 (the last built step) once identify/configure/dispatchable all read complete", () => {
    expect(
      resumeStepNumber(completion({ identify: true, configure: true, dispatchable: true })),
    ).toBe(4);
  });
});

describe("hostSetupMessage", () => {
  it("is null when both the control token and the transport record are present", () => {
    expect(hostSetupMessage({ tokenPresent: true, transportPresent: true })).toBeNull();
  });

  it("names only the missing control token", () => {
    expect(hostSetupMessage({ tokenPresent: false, transportPresent: true })).toBe(
      "Finish host setup — control token not set",
    );
  });

  it("names only the missing forge credential", () => {
    expect(hostSetupMessage({ tokenPresent: true, transportPresent: false })).toBe(
      "Finish host setup — forge credential not set",
    );
  });

  it("names both clauses when neither is set", () => {
    expect(hostSetupMessage({ tokenPresent: false, transportPresent: false })).toBe(
      "Finish host setup — forge credential not set / control token not set",
    );
  });
});

describe("operationalEngineKeys", () => {
  const engineKey = "acme/widget";

  it("is empty for empty workspace and project lists", () => {
    expect(operationalEngineKeys([], [])).toEqual([]);
  });

  it("includes a key via the bound-project arm alone", () => {
    expect(operationalEngineKeys([], [makeProjectListEntry({ id: "p1", engine_key: engineKey })])).toEqual([
      engineKey,
    ]);
  });

  it("includes a key via the PR-bearing-workspace arm alone", () => {
    expect(
      operationalEngineKeys([workspace({ engine_key: engineKey, pr_number: 7 })], []),
    ).toEqual([engineKey]);
  });

  it("excludes a project that only records intent, not binding", () => {
    expect(
      operationalEngineKeys(
        [],
        [makeProjectListEntry({ id: "p1", engine_key: null, intended_engine_key: engineKey })],
      ),
    ).toEqual([]);
  });

  it("excludes a workspace for this engine whose pr_number is null", () => {
    expect(
      operationalEngineKeys([workspace({ engine_key: engineKey, pr_number: null })], []),
    ).toEqual([]);
  });

  it("keys a PR-bearing workspace only to its own engine, not to an unrelated one", () => {
    const result = operationalEngineKeys(
      [workspace({ engine_key: "other/repo", pr_number: 7 })],
      [],
    );
    expect(result).toEqual(["other/repo"]);
    expect(result).not.toContain(engineKey);
  });

  it("de-duplicates when both arms fire for the same engine", () => {
    expect(
      operationalEngineKeys(
        [workspace({ engine_key: engineKey, pr_number: 7 })],
        [makeProjectListEntry({ id: "p1", engine_key: engineKey })],
      ),
    ).toEqual([engineKey]);
  });

  it("returns a sorted list across multiple operational engines", () => {
    expect(
      operationalEngineKeys(
        [workspace({ engine_key: "zzz/repo", pr_number: 1 })],
        [makeProjectListEntry({ id: "p1", engine_key: "aaa/repo" })],
      ),
    ).toEqual(["aaa/repo", "zzz/repo"]);
  });
});
