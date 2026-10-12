import { describe, expect, it } from "vitest";

import type { EnginePreflightFinding, OnboardingReadiness, OnboardingState } from "../../../api";
import { buildConnectStep3Rows } from "./connectStep3Rows";

function finding(overrides: Partial<EnginePreflightFinding> = {}): EnginePreflightFinding {
  return { check: "push_repository", status: "pass", message: "m", remedy: "", remedy_url: "", ...overrides };
}

function state(overrides: Partial<OnboardingState> = {}): OnboardingState {
  return {
    slug: "acme/widget", stack: "python", scaffold_pr_number: null, scaffold_pr_url: null,
    authoritative_checks: [], protection_applied_keys: [], protection_deferred: [], ...overrides,
  };
}

function readiness(
  checklist: EnginePreflightFinding[],
  stateOverrides: Partial<OnboardingState> = {},
): OnboardingReadiness {
  return { state: state(stateOverrides), checklist, overall: "fail" };
}

function rowByKey(rows: ReturnType<typeof buildConnectStep3Rows>, key: string) {
  const row = rows.find((r) => r.key === key);
  if (row === undefined) throw new Error(`no row for key ${key}`);
  return row;
}

describe("buildConnectStep3Rows — reachability", () => {
  it("maps a fail to the danger chip with the verbatim remedy", () => {
    const r = readiness([finding({ check: "push_repository", status: "fail", message: "no HEAD", remedy: "push it" })]);
    const row = rowByKey(buildConnectStep3Rows(r, false), "reachability");
    expect(row.status).toBe("fail");
    expect(row.detail).toBe("no HEAD");
    expect(row.remedy).toBe("push it");
  });

  it("maps unknown to unverifiable, never a raw backend status", () => {
    const r = readiness([finding({ check: "push_repository", status: "unknown", message: "no credential" })]);
    const row = rowByKey(buildConnectStep3Rows(r, false), "reachability");
    expect(row.status).toBe("unverifiable");
  });

  it("maps pass through unchanged", () => {
    const r = readiness([finding({ check: "push_repository", status: "pass" })]);
    expect(rowByKey(buildConnectStep3Rows(r, false), "reachability").status).toBe("pass");
  });
});

describe("buildConnectStep3Rows — workflow-file write permission", () => {
  it("maps a fail to the danger chip with the named cause and remedy", () => {
    const r = readiness([
      finding({
        check: "workflow_write", status: "fail",
        message: "this credential is a classic personal access token without the 'workflow' scope",
        remedy: "regenerate the token with the 'workflow' checkbox ticked",
      }),
    ]);
    const row = rowByKey(buildConnectStep3Rows(r, false), "workflow_write");
    expect(row.status).toBe("fail");
    expect(row.detail).toContain("workflow' scope");
    expect(row.remedy).toContain("regenerate the token");
  });

  it("maps unknown to unverifiable, carrying the remedy through (unlike reachability)", () => {
    const r = readiness([
      finding({
        check: "workflow_write", status: "unknown",
        message: "cannot verify — no transport credential yet",
        remedy: "mint a transport credential, then this check runs",
      }),
    ]);
    const row = rowByKey(buildConnectStep3Rows(r, false), "workflow_write");
    expect(row.status).toBe("unverifiable");
    expect(row.remedy).toContain("mint a transport credential");
  });

  it("maps pass through unchanged", () => {
    const r = readiness([finding({ check: "workflow_write", status: "pass" })]);
    expect(rowByKey(buildConnectStep3Rows(r, false), "workflow_write").status).toBe("pass");
  });

  it("is positioned between reachability and the scaffold row, matching backend order", () => {
    const r = readiness([
      finding({ check: "push_repository", status: "pass" }),
      finding({ check: "workflow_write", status: "pass" }),
      finding({ check: "review_and_merge_scaffold_pr", status: "pass", message: "PR #1 merged" }),
    ]);
    const rows = buildConnectStep3Rows(r, false);
    const keys = rows.map((row) => row.key);
    const reachIdx = keys.indexOf("reachability");
    const workflowIdx = keys.indexOf("workflow_write");
    const scaffoldIdx = keys.indexOf("scaffold");
    expect(reachIdx).toBeLessThan(workflowIdx);
    expect(workflowIdx).toBeLessThan(scaffoldIdx);
  });
});

describe("buildConnectStep3Rows — scaffold PR", () => {
  it("fail + no scaffold_pr_number yet -> not_started", () => {
    const r = readiness([finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "scaffold has not been run yet" })]);
    const row = rowByKey(buildConnectStep3Rows(r, false), "scaffold");
    expect(row.status).toBe("not_started");
    expect(row.linkUrl).toBeUndefined();
  });

  it("fail + scaffold_pr_number present -> waiting, with the live PR pill", () => {
    const r = readiness(
      [finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "PR #7 not yet merged (state=OPEN)" })],
      { scaffold_pr_number: 7, scaffold_pr_url: "https://github.com/acme/widget/pull/7" },
    );
    const row = rowByKey(buildConnectStep3Rows(r, false), "scaffold");
    expect(row.status).toBe("waiting");
    expect(row.linkUrl).toBe("https://github.com/acme/widget/pull/7");
  });

  it("pass carries the PR link forward", () => {
    const r = readiness(
      [finding({ check: "review_and_merge_scaffold_pr", status: "pass", message: "PR #7 merged" })],
      { scaffold_pr_number: 7, scaffold_pr_url: "https://github.com/acme/widget/pull/7" },
    );
    const row = rowByKey(buildConnectStep3Rows(r, false), "scaffold");
    expect(row.status).toBe("pass");
    expect(row.linkUrl).toBe("https://github.com/acme/widget/pull/7");
  });

  it("unknown (e.g. no credential to check PR state) maps to unverifiable", () => {
    const r = readiness(
      [finding({ check: "review_and_merge_scaffold_pr", status: "unknown", message: "no credential available to check PR state" })],
      { scaffold_pr_number: 7 },
    );
    expect(rowByKey(buildConnectStep3Rows(r, false), "scaffold").status).toBe("unverifiable");
  });
});

describe("buildConnectStep3Rows — reviewer access", () => {
  it("is always unverifiable, never pass/fail", () => {
    const r = readiness([finding({ check: "grant_reviewer_collaborator_access", status: "unknown", message: "cannot verify" })]);
    expect(rowByKey(buildConnectStep3Rows(r, false), "reviewer_access").status).toBe("unverifiable");
  });
});

describe("buildConnectStep3Rows — protection rows", () => {
  it("renders each applied key as a pass row", () => {
    const r = readiness([], { protection_applied_keys: ["squash_merge_enabled", "delete_branch_on_merge"] });
    const rows = buildConnectStep3Rows(r, false);
    expect(rowByKey(rows, "protection-squash_merge_enabled").status).toBe("pass");
    expect(rowByKey(rows, "protection-delete_branch_on_merge").status).toBe("pass");
  });

  it("renders each deferred finding with its verbatim remedy and a working settings link", () => {
    const r = readiness([], {
      protection_deferred: [
        finding({
          check: "required_checks", status: "fail", message: "does not satisfy: required checks",
          remedy: "add the check", remedy_url: "https://github.com/acme/widget/settings/branches",
        }),
      ],
    });
    const row = rowByKey(buildConnectStep3Rows(r, false), "protection-required_checks");
    expect(row.status).toBe("fail");
    expect(row.remedy).toBe("add the check");
    expect(row.linkUrl).toBe("https://github.com/acme/widget/settings/branches");
  });

  it("falls back to the raw key as the label for a protection key with no known display name", () => {
    const r = readiness([], { protection_applied_keys: ["some_future_row"] });
    const row = rowByKey(buildConnectStep3Rows(r, false), "protection-some_future_row");
    expect(row.label).toBe("some_future_row");
  });

  it("a deferred finding with no remedy_url renders no link", () => {
    const r = readiness([], {
      protection_deferred: [finding({ check: "required_checks", status: "fail", remedy_url: "" })],
    });
    const row = rowByKey(buildConnectStep3Rows(r, false), "protection-required_checks");
    expect(row.linkUrl).toBeUndefined();
  });

  it("names the admin-403 deferred finding the same way as any other deferred row", () => {
    const r = readiness([], {
      protection_deferred: [
        finding({
          check: "required_checks", status: "fail",
          message: "cannot read branch protection — token lacks admin scope",
          remedy: "grant the transport token admin access", remedy_url: "https://github.com/acme/widget/settings/branches",
        }),
      ],
    });
    const row = rowByKey(buildConnectStep3Rows(r, false), "protection-required_checks");
    expect(row.detail).toContain("admin scope");
    expect(row.linkUrl).not.toBeUndefined();
  });
});

describe("buildConnectStep3Rows — mint_credentials hoisting", () => {
  it("reviewer-only-missing: hoisted first with the verbatim remedy plus a Settings pointer", () => {
    const r = readiness([
      finding({ check: "mint_credentials", status: "fail", message: "no reviewer credential configured", remedy: "write a reviewer secret" }),
      finding({ check: "push_repository", status: "pass" }),
    ]);
    const rows = buildConnectStep3Rows(r, true);
    expect(rows[0]?.key).toBe("mint_credentials");
    expect(rows[0]?.status).toBe("fail");
    expect(rows[0]?.remedy).toContain("write a reviewer secret");
    expect(rows[0]?.remedy).toContain("Settings");
  });

  it("both credentials missing (credentialPresent false): no row at all — step 1's chip owns it", () => {
    const r = readiness([
      finding({ check: "mint_credentials", status: "fail", message: "no transport credential" }),
    ]);
    const rows = buildConnectStep3Rows(r, false);
    expect(rows.some((row) => row.key === "mint_credentials")).toBe(false);
  });

  it("mint_credentials pass never hoists a row", () => {
    const r = readiness([finding({ check: "mint_credentials", status: "pass" })]);
    const rows = buildConnectStep3Rows(r, true);
    expect(rows.some((row) => row.key === "mint_credentials")).toBe(false);
  });
});

describe("buildConnectStep3Rows — first entry renders zero danger chips", () => {
  it("a freshly registered, credential-less engine shows no fail rows", () => {
    const r = readiness([
      finding({ check: "push_repository", status: "unknown", message: "cannot verify — no transport credential yet" }),
      finding({ check: "mint_credentials", status: "fail", message: "no transport credential" }),
      finding({ check: "grant_reviewer_collaborator_access", status: "unknown", message: "cannot verify" }),
      finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "scaffold has not been run yet" }),
    ]);
    const rows = buildConnectStep3Rows(r, false);
    expect(rows.some((row) => row.status === "fail")).toBe(false);
  });
});
