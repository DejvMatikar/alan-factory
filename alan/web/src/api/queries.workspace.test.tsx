import { act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type WorkspaceDetail } from "../api";
import { renderHookWithQuery } from "../test/renderWithQuery";
import { makeWorkspaceDetail } from "../test/workspaceFixtures";
import {
  useRefreshWorkspaceDetail,
  useWorkspaceDetailQuery,
  useWorkspaceInvocationsQuery,
  useWorkspaceRepoKey,
} from "./queries";

function useWorkspaceState(taskId: string | undefined) {
  const detail = useWorkspaceDetailQuery(taskId);
  const list = useWorkspaceInvocationsQuery(
    taskId,
    detail.epoch,
    detail.data !== undefined && detail.data.team !== "triage",
  );
  const refresh = useRefreshWorkspaceDetail(taskId);
  return { detail, list, refresh };
}

function detailNamed(title: string, overrides: Partial<WorkspaceDetail> = {}): WorkspaceDetail {
  return makeWorkspaceDetail({ title, ...overrides });
}

function pending<T>() {
  const settlers: { resolve: (value: T) => void; reject: (reason: unknown) => void }[] = [];
  const next = () =>
    new Promise<T>((resolve, reject) => {
      settlers.push({ resolve, reject });
    });
  return { next, settlers };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("useWorkspaceDetailQuery epoch", () => {
  it("is zero before the first load and one after it", async () => {
    vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    expect(result.current.detail.epoch).toBe(0);
    await waitFor(() => {
      expect(result.current.detail.data?.title).toBe("T1");
    });
    expect(result.current.detail.epoch).toBe(1);
  });

  it("requests the workspace with the triage include", async () => {
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(result.current.detail.data).toBeDefined();
    });
    expect(getWorkspace).toHaveBeenCalledWith("t1", { includeTriage: true });
  });

  it("increases on every successful refresh", async () => {
    let calls = 0;
    vi.spyOn(api, "getWorkspace").mockImplementation(() => {
      calls += 1;
      return Promise.resolve(detailNamed(`T${String(calls)}`));
    });
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(1);
    });
    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(2);
    });
    expect(result.current.detail.data?.title).toBe("T2");
  });

  it("increases on each of two refreshes that land in the same millisecond", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-24T00:00:00Z"));
    let calls = 0;
    vi.spyOn(api, "getWorkspace").mockImplementation(() => {
      calls += 1;
      return Promise.resolve(detailNamed(`T${String(calls)}`));
    });
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(1);
    });
    const firstUpdatedAt = Date.now();
    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(2);
    });
    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(3);
    });
    expect(Date.now()).toBe(firstUpdatedAt);
  });

  it("does not increase when a refresh fails and reports the failure", async () => {
    const getWorkspace = vi
      .spyOn(api, "getWorkspace")
      .mockResolvedValueOnce(detailNamed("T1"))
      .mockRejectedValueOnce(new Error("refresh failed"));
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(1);
    });
    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.detail.isError).toBe(true);
    });
    expect(getWorkspace).toHaveBeenCalledTimes(2);
    expect(result.current.detail.epoch).toBe(1);
    expect(result.current.detail.data?.title).toBe("T1");
  });

  it("clears the failure once a later refresh succeeds", async () => {
    vi.spyOn(api, "getWorkspace")
      .mockResolvedValueOnce(detailNamed("T1"))
      .mockRejectedValueOnce(new Error("refresh failed"))
      .mockResolvedValueOnce(detailNamed("T3"));
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(1);
    });
    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.detail.isError).toBe(true);
    });
    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.detail.isError).toBe(false);
    });
    expect(result.current.detail.data?.title).toBe("T3");
    expect(result.current.detail.epoch).toBe(2);
  });

  it("drops the previous task's data while a new task loads", async () => {
    const workspaces = pending<WorkspaceDetail>();
    vi.spyOn(api, "getWorkspace").mockImplementation((taskId) =>
      taskId === "t1" ? Promise.resolve(detailNamed("T1")) : workspaces.next(),
    );
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result, rerender } = renderHookWithQuery(
      ({ taskId }: { taskId: string }) => useWorkspaceState(taskId),
      { initialProps: { taskId: "t1" } },
    );
    await waitFor(() => {
      expect(result.current.detail.data?.title).toBe("T1");
    });
    rerender({ taskId: "t2" });
    expect(result.current.detail.data).toBeUndefined();
    expect(result.current.detail.epoch).toBe(0);
  });

  it("stays idle without a task id", async () => {
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    const { result } = renderHookWithQuery(() => useWorkspaceState(undefined));
    act(() => {
      result.current.refresh();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(getWorkspace).not.toHaveBeenCalled();
    expect(result.current.detail.data).toBeUndefined();
  });
});

describe("useWorkspaceRepoKey", () => {
  it("is undefined before the workspace loads and its engine key after", async () => {
    vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1", { engine_key: "acme/widget" }));
    const { result } = renderHookWithQuery(() => useWorkspaceRepoKey("t1"));
    expect(result.current).toBeUndefined();
    await waitFor(() => {
      expect(result.current).toBe("acme/widget");
    });
  });

  it("is null for a workspace with no engine key", async () => {
    vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1", { engine_key: null }));
    const { result } = renderHookWithQuery(() => useWorkspaceRepoKey("t1"));
    await waitFor(() => {
      expect(result.current).toBeNull();
    });
  });

  it("is null for a workspace that omits the engine key", async () => {
    vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    const { result } = renderHookWithQuery(() => useWorkspaceRepoKey("t1"));
    await waitFor(() => {
      expect(result.current).toBeNull();
    });
  });

  it("requests the workspace without the triage include", async () => {
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    const { result } = renderHookWithQuery(() => useWorkspaceRepoKey("t1"));
    await waitFor(() => {
      expect(result.current).toBeNull();
    });
    expect(getWorkspace).toHaveBeenCalledExactlyOnceWith("t1");
  });

  it("stays idle without a task id", async () => {
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    const { result } = renderHookWithQuery(() => useWorkspaceRepoKey(undefined));
    await act(async () => {
      await Promise.resolve();
    });
    expect(getWorkspace).not.toHaveBeenCalled();
    expect(result.current).toBeUndefined();
  });

  it("stays undefined when the read fails", async () => {
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockRejectedValue(new Error("boom"));
    const { result } = renderHookWithQuery(() => useWorkspaceRepoKey("t1"));
    await waitFor(() => {
      expect(getWorkspace).toHaveBeenCalledOnce();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current).toBeUndefined();
  });

  it("drops the previous task's key while a new task loads", async () => {
    const workspaces = pending<WorkspaceDetail>();
    vi.spyOn(api, "getWorkspace").mockImplementation((taskId) =>
      taskId === "t1"
        ? Promise.resolve(detailNamed("T1", { engine_key: "acme/widget" }))
        : workspaces.next(),
    );
    const { result, rerender } = renderHookWithQuery(
      ({ taskId }: { taskId: string }) => useWorkspaceRepoKey(taskId),
      { initialProps: { taskId: "t1" } },
    );
    await waitFor(() => {
      expect(result.current).toBe("acme/widget");
    });
    rerender({ taskId: "t2" });
    expect(result.current).toBeUndefined();
  });

  it("does not disturb the detail query's epoch or its triage read", async () => {
    const getWorkspace = vi
      .spyOn(api, "getWorkspace")
      .mockResolvedValue(detailNamed("T1", { engine_key: "acme/widget" }));
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => ({
      state: useWorkspaceState("t1"),
      repoKey: useWorkspaceRepoKey("t1"),
    }));
    await waitFor(() => {
      expect(result.current.repoKey).toBe("acme/widget");
    });
    await waitFor(() => {
      expect(result.current.state.detail.epoch).toBe(1);
    });
    expect(getWorkspace).toHaveBeenCalledWith("t1", { includeTriage: true });
    expect(getWorkspace).toHaveBeenCalledWith("t1");
    expect(result.current.state.detail.data?.title).toBe("T1");
  });

  it("reads the workspace once for any number of consumers", async () => {
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    const { result } = renderHookWithQuery(() => [useWorkspaceRepoKey("t1"), useWorkspaceRepoKey("t1")]);
    await waitFor(() => {
      expect(result.current).toEqual([null, null]);
    });
    expect(getWorkspace).toHaveBeenCalledOnce();
  });
});

describe("useRefreshWorkspaceDetail", () => {
  it("cancels a first load that is still in flight and keeps only the newer read", async () => {
    const workspaces = pending<WorkspaceDetail>();
    vi.spyOn(api, "getWorkspace").mockImplementation(() => workspaces.next());
    vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(workspaces.settlers).toHaveLength(1);
    });

    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(workspaces.settlers).toHaveLength(2);
    });
    await act(async () => {
      workspaces.settlers[1]!.resolve(detailNamed("newer"));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.detail.data?.title).toBe("newer");
    });

    await act(async () => {
      workspaces.settlers[0]!.reject(new Error("stale rejection"));
      await Promise.resolve();
    });
    expect(result.current.detail.isError).toBe(false);
    expect(result.current.detail.data?.title).toBe("newer");
  });

  it("cancels the invocation list of the previous epoch so a late list is discarded", async () => {
    const lists = pending<string[]>();
    vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    vi.spyOn(api, "listInvocations").mockImplementation(() => lists.next());
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(lists.settlers).toHaveLength(1);
    });

    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(lists.settlers).toHaveLength(2);
    });
    await act(async () => {
      lists.settlers[1]!.resolve(["inv_current"]);
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.list.data).toEqual(["inv_current"]);
    });

    await act(async () => {
      lists.settlers[0]!.resolve(["inv_stale"]);
      await Promise.resolve();
    });
    expect(result.current.list.data).toEqual(["inv_current"]);
  });

  it("empties the invocation list until the next epoch's list arrives", async () => {
    const lists = pending<string[]>();
    vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    vi.spyOn(api, "listInvocations").mockImplementation(() => lists.next());
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(lists.settlers).toHaveLength(1);
    });
    await act(async () => {
      lists.settlers[0]!.resolve(["inv_001"]);
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.list.data).toEqual(["inv_001"]);
    });

    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(2);
    });
    expect(result.current.list.data).toBeUndefined();
    await act(async () => {
      lists.settlers[1]!.resolve(["inv_001", "inv_002"]);
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.list.data).toEqual(["inv_001", "inv_002"]);
    });
  });

  it("surfaces an invocation-list failure on the list view", async () => {
    vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1"));
    vi.spyOn(api, "listInvocations").mockRejectedValue(new Error("list failed"));
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(result.current.list.isError).toBe(true);
    });
    expect(result.current.list.data).toBeUndefined();
  });

  it("never requests the invocation list for a triage workspace, even after a refresh", async () => {
    vi.spyOn(api, "getWorkspace").mockResolvedValue(detailNamed("T1", { team: "triage" }));
    const listInvocations = vi.spyOn(api, "listInvocations").mockResolvedValue([]);
    const { result } = renderHookWithQuery(() => useWorkspaceState("t1"));
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(1);
    });
    act(() => {
      result.current.refresh();
    });
    await waitFor(() => {
      expect(result.current.detail.epoch).toBe(2);
    });
    expect(listInvocations).not.toHaveBeenCalled();
  });
});
