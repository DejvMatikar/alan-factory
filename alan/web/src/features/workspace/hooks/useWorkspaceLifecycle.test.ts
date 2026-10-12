import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../../api";
import { makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import { stopResumeModeFor, useWorkspaceLifecycle } from "./useWorkspaceLifecycle";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("stopResumeModeFor", () => {
  it("returns 'resume' when paused", () => {
    expect(stopResumeModeFor(makeWorkspaceDetail({ status: "paused" }))).toBe("resume");
  });

  it("returns 'requeue' when awaiting_input with a resumable failed job", () => {
    expect(
      stopResumeModeFor(
        makeWorkspaceDetail({ status: "awaiting_input", has_resumable_failed_job: true }),
      ),
    ).toBe("requeue");
  });

  it("returns 'stop' when active with a running job", () => {
    expect(
      stopResumeModeFor(makeWorkspaceDetail({ status: "active", has_running_job: true })),
    ).toBe("stop");
  });

  it("returns null otherwise", () => {
    expect(stopResumeModeFor(makeWorkspaceDetail({ status: "active" }))).toBeNull();
  });
});

describe("useWorkspaceLifecycle — archive toggle", () => {
  it("flags busy, calls archiveWorkspace and notifies onChanged on success", async () => {
    const onChanged = vi.fn();
    const spy = vi.spyOn(api, "archiveWorkspace").mockResolvedValue(undefined);
    const ws = makeWorkspaceDetail({ status: "active" });
    const { result } = renderHook(() => useWorkspaceLifecycle("t1", ws, onChanged));

    act(() => {
      result.current.handleToggle();
    });
    expect(result.current.toggleBusy).toBe(true);
    expect(result.current.pendingIsArchived).toBe(false);

    await act(async () => {
      await Promise.resolve();
    });
    expect(spy).toHaveBeenCalledWith("t1");
    expect(result.current.toggleBusy).toBe(false);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("calls unarchiveWorkspace when the workspace is archived", async () => {
    const onChanged = vi.fn();
    const spy = vi.spyOn(api, "unarchiveWorkspace").mockResolvedValue(undefined);
    const ws = makeWorkspaceDetail({ status: "archived" });
    const { result } = renderHook(() => useWorkspaceLifecycle("t1", ws, onChanged));

    act(() => {
      result.current.handleToggle();
    });
    expect(result.current.pendingIsArchived).toBe(true);

    await act(async () => {
      await Promise.resolve();
    });
    expect(spy).toHaveBeenCalledWith("t1");
  });

  it("surfaces an error message and clears busy on failure", async () => {
    vi.spyOn(api, "archiveWorkspace").mockRejectedValue(new Error("boom"));
    const ws = makeWorkspaceDetail({ status: "active" });
    const { result } = renderHook(() => useWorkspaceLifecycle("t1", ws, vi.fn()));

    act(() => {
      result.current.handleToggle();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.toggleBusy).toBe(false);
    expect(result.current.toggleError).toBe("boom");
  });
});

describe("useWorkspaceLifecycle — stop/resume", () => {
  it("calls stopWorkspace when the mode is 'stop'", async () => {
    const onChanged = vi.fn();
    const spy = vi.spyOn(api, "stopWorkspace").mockResolvedValue(undefined);
    const ws = makeWorkspaceDetail({ status: "active", has_running_job: true });
    const { result } = renderHook(() => useWorkspaceLifecycle("t1", ws, onChanged));

    expect(result.current.stopResumeMode).toBe("stop");
    act(() => {
      result.current.handleStopResume();
    });
    expect(result.current.stopResumeBusy).toBe(true);

    await act(async () => {
      await Promise.resolve();
    });
    expect(spy).toHaveBeenCalledWith("t1");
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("calls resumeWorkspace when the mode is 'resume'", async () => {
    const spy = vi.spyOn(api, "resumeWorkspace").mockResolvedValue(undefined);
    const ws = makeWorkspaceDetail({ status: "paused" });
    const { result } = renderHook(() => useWorkspaceLifecycle("t1", ws, vi.fn()));

    act(() => {
      result.current.handleStopResume();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(spy).toHaveBeenCalledWith("t1");
  });

  it("surfaces a stop/resume error and clears busy", async () => {
    vi.spyOn(api, "stopWorkspace").mockRejectedValue(new Error("stop-boom"));
    const ws = makeWorkspaceDetail({ status: "active", has_running_job: true });
    const { result } = renderHook(() => useWorkspaceLifecycle("t1", ws, vi.fn()));

    act(() => {
      result.current.handleStopResume();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.stopResumeBusy).toBe(false);
    expect(result.current.stopResumeError).toBe("stop-boom");
  });
});

describe("useWorkspaceLifecycle — delete dialog", () => {
  it("opens and closes the delete dialog", () => {
    const ws = makeWorkspaceDetail({ status: "active" });
    const { result } = renderHook(() => useWorkspaceLifecycle("t1", ws, vi.fn()));

    expect(result.current.deleteOpen).toBe(false);
    act(() => {
      result.current.openDelete();
    });
    expect(result.current.deleteOpen).toBe(true);
    act(() => {
      result.current.closeDelete();
    });
    expect(result.current.deleteOpen).toBe(false);
  });
});
