import { describe, expect, it } from "vitest";

import type { EnginePreflightFinding, OnboardingReadiness } from "../api";
import {
  DISPATCHABLE_STEP_KEY,
  WIZARD_STEPS,
  deriveCompletion,
  isConfigureDone,
  isDispatchableReady,
  selectStep,
} from "./wizardStepSelection";

function finding(overrides: Partial<EnginePreflightFinding> = {}): EnginePreflightFinding {
  return { check: "push_repository", status: "pass", message: "m", remedy: "", remedy_url: "", ...overrides };
}

function readiness(overrides: Partial<OnboardingReadiness> = {}): OnboardingReadiness {
  return {
    state: {
      slug: "acme/widget",
      stack: "python",
      scaffold_pr_number: null,
      scaffold_pr_url: null,
      authoritative_checks: [],
      protection_applied_keys: [],
      protection_deferred: [],
    },
    checklist: [],
    overall: "fail",
    ...overrides,
  };
}

describe("isDispatchableReady", () => {
  it("is false when readiness is null", () => {
    expect(isDispatchableReady(null)).toBe(false);
  });

  it("is false when the scaffold PR is not yet merged", () => {
    const r = readiness({
      checklist: [finding({ check: "review_and_merge_scaffold_pr", status: "fail" })],
    });
    expect(isDispatchableReady(r)).toBe(false);
  });

  it("is false when merged but protect() has never actually run (both applied and deferred still empty)", () => {
    const r = readiness({
      checklist: [finding({ check: "review_and_merge_scaffold_pr", status: "pass" })],
      state: {
        slug: "acme/widget", stack: "python", scaffold_pr_number: 7,
        scaffold_pr_url: "https://github.com/acme/widget/pull/7",
        authoritative_checks: [], protection_applied_keys: [], protection_deferred: [],
      },
    });
    expect(isDispatchableReady(r)).toBe(false);
  });

  it("is false when merged but a protection finding is still deferred", () => {
    const r = readiness({
      checklist: [finding({ check: "review_and_merge_scaffold_pr", status: "pass" })],
      state: {
        slug: "acme/widget", stack: "python", scaffold_pr_number: 7,
        scaffold_pr_url: "https://github.com/acme/widget/pull/7",
        authoritative_checks: [], protection_applied_keys: [],
        protection_deferred: [finding({ check: "required_checks", status: "fail" })],
      },
    });
    expect(isDispatchableReady(r)).toBe(false);
  });

  it("is true once merged and no protection finding is deferred", () => {
    const r = readiness({
      checklist: [finding({ check: "review_and_merge_scaffold_pr", status: "pass" })],
      state: {
        slug: "acme/widget", stack: "python", scaffold_pr_number: 7,
        scaffold_pr_url: "https://github.com/acme/widget/pull/7",
        authoritative_checks: [], protection_applied_keys: ["squash_merge_enabled"],
        protection_deferred: [],
      },
    });
    expect(isDispatchableReady(r)).toBe(true);
  });
});

describe("isConfigureDone", () => {
  it("is false when readiness is null", () => {
    expect(isConfigureDone(null)).toBe(false);
  });

  it("is false when no scaffold PR exists yet and dispatchable isn't ready either", () => {
    expect(isConfigureDone(readiness())).toBe(false);
  });

  it("is true once a scaffold PR exists, even before it merges", () => {
    const r = readiness({
      state: {
        slug: "acme/widget", stack: "python", scaffold_pr_number: 7,
        scaffold_pr_url: "https://github.com/acme/widget/pull/7",
        authoritative_checks: [], protection_applied_keys: [], protection_deferred: [],
      },
    });
    expect(isConfigureDone(r)).toBe(true);
  });

  it("is true whenever dispatchable is ready, via the second disjunct", () => {
    const r = readiness({
      checklist: [finding({ check: "review_and_merge_scaffold_pr", status: "pass" })],
      state: {
        slug: "acme/widget", stack: "python", scaffold_pr_number: 7,
        scaffold_pr_url: "https://github.com/acme/widget/pull/7",
        authoritative_checks: [], protection_applied_keys: ["squash_merge_enabled"],
        protection_deferred: [],
      },
    });
    expect(isDispatchableReady(r)).toBe(true);
    expect(isConfigureDone(r)).toBe(true);
  });
});

describe("deriveCompletion", () => {
  it("keys step 1 completion on registered AND credentialPresent only", () => {
    expect(deriveCompletion({ registered: true, credentialPresent: false, readiness: null }).identify).toBe(false);
    expect(deriveCompletion({ registered: false, credentialPresent: true, readiness: null }).identify).toBe(false);
    expect(deriveCompletion({ registered: true, credentialPresent: true, readiness: null }).identify).toBe(true);
  });

  it("configure completion mirrors isConfigureDone, not a hardcoded false", () => {
    expect(deriveCompletion({ registered: true, credentialPresent: true, readiness: null }).configure).toBe(false);
    const r = readiness({
      state: {
        slug: "acme/widget", stack: "python", scaffold_pr_number: 7,
        scaffold_pr_url: "https://github.com/acme/widget/pull/7",
        authoritative_checks: [], protection_applied_keys: [], protection_deferred: [],
      },
    });
    expect(deriveCompletion({ registered: true, credentialPresent: true, readiness: r }).configure).toBe(true);
  });

  it("dispatchable and identify derivations are unchanged by the configure wiring", () => {
    const r = readiness({
      checklist: [finding({ check: "review_and_merge_scaffold_pr", status: "pass" })],
      state: {
        slug: "acme/widget", stack: "python", scaffold_pr_number: 7,
        scaffold_pr_url: "https://github.com/acme/widget/pull/7",
        authoritative_checks: [], protection_applied_keys: ["squash_merge_enabled"],
        protection_deferred: [],
      },
    });
    const completion = deriveCompletion({ registered: true, credentialPresent: true, readiness: r });
    expect(completion.identify).toBe(true);
    expect(completion.dispatchable).toBe(true);
  });

  it("posture is always incomplete regardless of input shape — there is no server signal for wizard-finished", () => {
    expect(deriveCompletion({ registered: false, credentialPresent: false, readiness: null }).posture).toBe(false);
    expect(deriveCompletion({ registered: true, credentialPresent: true, readiness: null }).posture).toBe(false);
    const r = readiness({
      checklist: [finding({ check: "review_and_merge_scaffold_pr", status: "pass" })],
      state: {
        slug: "acme/widget", stack: "python", scaffold_pr_number: 7,
        scaffold_pr_url: "https://github.com/acme/widget/pull/7",
        authoritative_checks: [], protection_applied_keys: ["squash_merge_enabled"],
        protection_deferred: [],
      },
    });
    expect(deriveCompletion({ registered: true, credentialPresent: true, readiness: r }).posture).toBe(false);
  });
});

describe("WIZARD_STEPS", () => {
  it("registers posture as built", () => {
    expect(WIZARD_STEPS.find((s) => s.key === "posture")?.built).toBe(true);
  });

  it("registers configure as built", () => {
    expect(WIZARD_STEPS.find((s) => s.key === "configure")?.built).toBe(true);
  });

  it("registers all four steps as built", () => {
    expect(WIZARD_STEPS.every((s) => s.built)).toBe(true);
  });
});

describe("selectStep", () => {
  const notReady: ReturnType<typeof deriveCompletion> = {
    identify: false, configure: false, dispatchable: false, posture: false,
  };
  const step1Done: ReturnType<typeof deriveCompletion> = {
    identify: true, configure: false, dispatchable: false, posture: false,
  };
  const step2Done: ReturnType<typeof deriveCompletion> = {
    identify: true, configure: true, dispatchable: false, posture: false,
  };
  const allDone: ReturnType<typeof deriveCompletion> = {
    identify: true, configure: true, dispatchable: true, posture: false,
  };

  it("derives the first incomplete built step when no override is requested", () => {
    expect(selectStep(WIZARD_STEPS, notReady, undefined)).toBe("identify");
    expect(selectStep(WIZARD_STEPS, step1Done, undefined)).toBe("configure");
    expect(selectStep(WIZARD_STEPS, step2Done, undefined)).toBe(DISPATCHABLE_STEP_KEY);
  });

  it("lands on posture — the only remaining built step — once dispatchable completes", () => {
    expect(selectStep(WIZARD_STEPS, allDone, undefined)).toBe("posture");
  });

  it("falls back to the last built step once every built step (including posture) reads complete", () => {
    const everythingComplete: ReturnType<typeof deriveCompletion> = {
      identify: true, configure: true, dispatchable: true, posture: true,
    };
    expect(selectStep(WIZARD_STEPS, everythingComplete, undefined)).toBe("posture");
  });

  it("honors a requested key that names a built step, regardless of completion", () => {
    expect(selectStep(WIZARD_STEPS, notReady, DISPATCHABLE_STEP_KEY)).toBe(DISPATCHABLE_STEP_KEY);
  });

  it("honors a configure deep link even before step 1 completes — override ignores completion entirely", () => {
    expect(selectStep(WIZARD_STEPS, notReady, "configure")).toBe("configure");
  });

  it("honors a posture deep link even when earlier built steps aren't complete", () => {
    expect(selectStep(WIZARD_STEPS, notReady, "posture")).toBe("posture");
  });

  it("falls through to the derived step for an unbuilt requested key — using a synthetic steps array, since every real WIZARD_STEPS entry is built today", () => {
    const stepsWithUnbuilt = [
      { key: "a", label: "A", built: true },
      { key: "b", label: "B", built: false },
    ];
    const completion = { a: false, b: false };
    expect(selectStep(stepsWithUnbuilt, completion, "b")).toBe("a");
  });

  it("falls through to the derived step for an unknown requested key", () => {
    expect(selectStep(WIZARD_STEPS, notReady, "not-a-real-step")).toBe("identify");
  });
});
