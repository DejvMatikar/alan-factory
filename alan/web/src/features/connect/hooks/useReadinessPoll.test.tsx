import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type EnginePreflightFinding, type OnboardingReadiness, type OnboardingState } from "../../../api";
import {
  READINESS_POLL_ACTIVE_CAPTION,
  READINESS_POLL_BASE_MS,
  READINESS_POLL_IDLE_CAPTION,
  READINESS_POLL_MAX_MS,
  READINESS_POLL_PROTECTING_CAPTION,
  nextPollDelay,
  useReadinessPoll,
} from "./useReadinessPoll";

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

const NOT_MERGED = readiness(
  [finding({ check: "review_and_merge_scaffold_pr", status: "fail", message: "not yet merged" })],
  { scaffold_pr_number: 7, scaffold_pr_url: "https://github.com/acme/widget/pull/7" },
);

const MERGED_UNPROTECTED = readiness(
  [finding({ check: "review_and_merge_scaffold_pr", status: "pass" })],
  {
    scaffold_pr_number: 7,
    protection_deferred: [finding({ check: "required_checks", status: "fail" })],
  },
);

const MERGED_PROTECTED = readiness(
  [finding({ check: "review_and_merge_scaffold_pr", status: "pass" })],
  { scaffold_pr_number: 7, protection_applied_keys: ["squash_merge_enabled"] },
);

describe("nextPollDelay", () => {
  it("resets to base on a healthy tick", () => {
    expect(nextPollDelay(READINESS_POLL_MAX_MS, true)).toBe(READINESS_POLL_BASE_MS);
  });

  it("doubles on a failed tick", () => {
    expect(nextPollDelay(READINESS_POLL_BASE_MS, false)).toBe(READINESS_POLL_BASE_MS * 2);
  });

  it("caps the doubling at the max", () => {
    expect(nextPollDelay(READINESS_POLL_MAX_MS, false)).toBe(READINESS_POLL_MAX_MS);
  });
});

describe("useReadinessPoll — gate and caption", () => {
  it("is idle with no timer when readiness is null", () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const { result } = renderHook(() =>
      useReadinessPoll({ engineKey: "acme/widget", readiness: null, refetch, hasError: false }),
    );
    expect(result.current).toBe(READINESS_POLL_IDLE_CAPTION);
    vi.advanceTimersByTime(READINESS_POLL_MAX_MS * 4);
    expect(refetch).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("is idle with no timer when scaffold has not been run yet (no PR number)", () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const noPr = readiness([finding({ check: "review_and_merge_scaffold_pr", status: "fail" })]);
    renderHook(() => useReadinessPoll({ engineKey: "acme/widget", readiness: noPr, refetch, hasError: false }));
    vi.advanceTimersByTime(READINESS_POLL_MAX_MS * 4);
    expect(refetch).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("is idle once dispatchable-ready (merged and protected) even with a PR number", () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const { result } = renderHook(() =>
      useReadinessPoll({ engineKey: "acme/widget", readiness: MERGED_PROTECTED, refetch, hasError: false }),
    );
    expect(result.current).toBe(READINESS_POLL_IDLE_CAPTION);
    vi.advanceTimersByTime(READINESS_POLL_MAX_MS * 4);
    expect(refetch).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});

describe("useReadinessPoll — active loop", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls at the base cadence while a PR is open and unmerged", () => {
    const refetch = vi.fn();
    const { result } = renderHook(() =>
      useReadinessPoll({ engineKey: "acme/widget", readiness: NOT_MERGED, refetch, hasError: false }),
    );
    expect(result.current).toBe(READINESS_POLL_ACTIVE_CAPTION);
    expect(refetch).not.toHaveBeenCalled();
    vi.advanceTimersByTime(READINESS_POLL_BASE_MS);
    expect(refetch).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(READINESS_POLL_BASE_MS);
    expect(refetch).toHaveBeenCalledTimes(2);
  });

  it("backs off (doubling, capped) across ticks that report an error", () => {
    const refetch = vi.fn();
    renderHook(() =>
      useReadinessPoll({ engineKey: "acme/widget", readiness: NOT_MERGED, refetch, hasError: true }),
    );
    vi.advanceTimersByTime(READINESS_POLL_BASE_MS);
    expect(refetch).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(READINESS_POLL_BASE_MS);
    expect(refetch).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(READINESS_POLL_BASE_MS);
    expect(refetch).toHaveBeenCalledTimes(2);
  });

  it("refetches immediately on visibilitychange -> visible, the deterministic e2e seam", () => {
    const refetch = vi.fn();
    renderHook(() =>
      useReadinessPoll({ engineKey: "acme/widget", readiness: NOT_MERGED, refetch, hasError: false }),
    );
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(refetch).not.toHaveBeenCalled();

    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(refetch).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
  });

  it("resets the backoff delay once the gate goes inactive, so a later re-entry starts back at base cadence", () => {
    const refetch = vi.fn();
    const { rerender } = renderHook<string, { readiness: OnboardingReadiness | null }>(
      ({ readiness: r }) =>
        useReadinessPoll({ engineKey: "acme/widget", readiness: r, refetch, hasError: true }),
      { initialProps: { readiness: NOT_MERGED } },
    );
    vi.advanceTimersByTime(READINESS_POLL_BASE_MS);
    expect(refetch).toHaveBeenCalledTimes(1);

    rerender({ readiness: null });
    rerender({ readiness: NOT_MERGED });

    vi.advanceTimersByTime(READINESS_POLL_BASE_MS);
    expect(refetch).toHaveBeenCalledTimes(2);
  });

  it("clears its timer on unmount", () => {
    const refetch = vi.fn();
    const { unmount } = renderHook(() =>
      useReadinessPoll({ engineKey: "acme/widget", readiness: NOT_MERGED, refetch, hasError: false }),
    );
    unmount();
    vi.advanceTimersByTime(READINESS_POLL_MAX_MS * 4);
    expect(refetch).not.toHaveBeenCalled();
  });
});

describe("useReadinessPoll — merge-detection fires protect", () => {
  it("fires protectEngine once merge is detected and refetches once it settles", async () => {
    const refetch = vi.fn();
    let resolveProtect!: () => void;
    const spy = vi.spyOn(api, "protectEngine").mockReturnValue(
      new Promise((resolve) => {
        resolveProtect = () => resolve({ ...state(), scaffold_pr_number: 7 });
      }),
    );
    const { result, rerender } = renderHook(
      ({ readiness: r }: { readiness: OnboardingReadiness }) =>
        useReadinessPoll({ engineKey: "acme/widget", readiness: r, refetch, hasError: false }),
      { initialProps: { readiness: NOT_MERGED } },
    );
    rerender({ readiness: MERGED_UNPROTECTED });

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith("acme/widget");
    });
    expect(result.current).toBe(READINESS_POLL_PROTECTING_CAPTION);

    resolveProtect();
    await waitFor(() => {
      expect(refetch).toHaveBeenCalledTimes(1);
    });
    spy.mockRestore();
  });

  it("never fires protect twice for the same detected merge while the first call is in flight", async () => {
    const refetch = vi.fn();
    let resolveProtect!: () => void;
    const spy = vi.spyOn(api, "protectEngine").mockReturnValue(
      new Promise((resolve) => {
        resolveProtect = () => resolve({ ...state(), scaffold_pr_number: 7 });
      }),
    );
    const { rerender } = renderHook(
      ({ readiness: r }: { readiness: OnboardingReadiness }) =>
        useReadinessPoll({ engineKey: "acme/widget", readiness: r, refetch, hasError: false }),
      { initialProps: { readiness: NOT_MERGED } },
    );
    rerender({ readiness: MERGED_UNPROTECTED });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    rerender({ readiness: { ...MERGED_UNPROTECTED } });
    expect(spy).toHaveBeenCalledTimes(1);

    resolveProtect();
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
    spy.mockRestore();
  });

  it("is re-armable: fires protect again on a later tick if still unprotected after the first call settles", async () => {
    const refetch = vi.fn();
    const spy = vi
      .spyOn(api, "protectEngine")
      .mockResolvedValue({ ...state(), scaffold_pr_number: 7 });
    const { rerender } = renderHook(
      ({ readiness: r }: { readiness: OnboardingReadiness }) =>
        useReadinessPoll({ engineKey: "acme/widget", readiness: r, refetch, hasError: false }),
      { initialProps: { readiness: NOT_MERGED } },
    );
    rerender({ readiness: MERGED_UNPROTECTED });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));

    rerender({ readiness: { ...MERGED_UNPROTECTED } });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    spy.mockRestore();
  });

  it("does not fire protect once fully dispatchable-ready", () => {
    const refetch = vi.fn();
    const spy = vi.spyOn(api, "protectEngine");
    renderHook(() =>
      useReadinessPoll({ engineKey: "acme/widget", readiness: MERGED_PROTECTED, refetch, hasError: false }),
    );
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
