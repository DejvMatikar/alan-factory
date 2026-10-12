import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { ProjectListEntry, WorkspaceSummary } from "../../../api";
import { server } from "../../../test/server";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import { useHomeData } from "./useHomeData";

function ws(overrides: Partial<WorkspaceSummary> & Pick<WorkspaceSummary, "task_id">): WorkspaceSummary {
  return makeWorkspaceSummary({ title: overrides.task_id, project_id: null, engine_key: null, ...overrides });
}

function mockWorkspaces(active: WorkspaceSummary[], archived: WorkspaceSummary[] = []): void {
  server.use(
    http.get("/api/workspaces", ({ request }) => {
      const bucket = new URL(request.url).searchParams.get("bucket");
      return HttpResponse.json(bucket === "archived" ? archived : active);
    }),
  );
}

function mockProjects(projects: ProjectListEntry[]): void {
  server.use(http.get("/api/projects", () => HttpResponse.json(projects)));
}

describe("useHomeData", () => {
  it("loads active-tab workspaces and projects", async () => {
    mockWorkspaces([ws({ task_id: "w1" })]);
    mockProjects([]);
    const { result } = renderHook(() => useHomeData("active"));
    await waitFor(() => expect(result.current.workspaces).not.toBeNull());
    expect(result.current.workspaces?.map((w) => w.task_id)).toEqual(["w1"]);
    expect(result.current.projects).toEqual([]);
    expect(result.current.loadError).toBeNull();
  });

  it("loads the archived bucket on the archived tab, leaving liveWorkspaces sourced from the active bucket", async () => {
    mockWorkspaces([ws({ task_id: "active-ws" })], [ws({ task_id: "arch-ws" })]);
    mockProjects([]);
    const { result } = renderHook(() => useHomeData("archived"));
    await waitFor(() => expect(result.current.workspaces).not.toBeNull());
    expect(result.current.workspaces?.map((w) => w.task_id)).toEqual(["arch-ws"]);
  });

  it("liveWorkspaces stays populated regardless of which tab is selected", async () => {
    mockWorkspaces([ws({ task_id: "active-ws" })], [ws({ task_id: "arch-ws" })]);
    mockProjects([]);
    const { result } = renderHook(() => useHomeData("active"));
    await waitFor(() => expect(result.current.liveWorkspaces).not.toBeNull());
    expect(result.current.liveWorkspaces?.map((w) => w.task_id)).toEqual(["active-ws"]);
  });

  it("sorts workspaces by created_at descending", async () => {
    mockWorkspaces([
      ws({ task_id: "older", created_at: "2026-01-01T00:00:00Z" }),
      ws({ task_id: "newer", created_at: "2026-06-01T00:00:00Z" }),
    ]);
    mockProjects([]);
    const { result } = renderHook(() => useHomeData("active"));
    await waitFor(() => expect(result.current.workspaces).not.toBeNull());
    expect(result.current.workspaces?.map((w) => w.task_id)).toEqual(["newer", "older"]);
  });

  it("keeps an independent error slot per source — a projects success does not clear a live workspaces error", async () => {
    server.use(
      http.get("/api/workspaces", () => HttpResponse.json({ detail: "ws-boom" }, { status: 500 })),
      http.get("/api/projects", () => HttpResponse.json([])),
    );
    const { result } = renderHook(() => useHomeData("active"));
    await waitFor(() => expect(result.current.loadError).toContain("ws-boom"));
    await waitFor(() => expect(result.current.projects).toEqual([]));
    expect(result.current.loadError).toContain("ws-boom");
  });

  it("reports a non-Error load failure via String(err)", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.listWorkspaces;
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    apiModule.api.listWorkspaces = () => Promise.reject("string-load-error");
    try {
      mockProjects([]);
      const { result } = renderHook(() => useHomeData("active"));
      await waitFor(() => expect(result.current.loadError).toBe("string-load-error"));
    } finally {
      apiModule.api.listWorkspaces = original;
    }
  });

  it("ignores a fetch result that resolves after unmount", () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    const promise = new Promise<void>((resolve) => {
      gate.resolve = resolve;
    });
    server.use(
      http.get("/api/workspaces", async () => {
        await promise;
        return HttpResponse.json([]);
      }),
    );
    mockProjects([]);
    const { unmount } = renderHook(() => useHomeData("active"));
    unmount();
    gate.resolve?.();
  });

  it("ignores a fetch error that rejects after unmount", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.listWorkspaces;
    const gate: { reject: ((reason?: unknown) => void) | null } = { reject: null };
    apiModule.api.listWorkspaces = () =>
      new Promise((_resolve, reject) => {
        gate.reject = reject;
      });
    try {
      mockProjects([]);
      const { unmount } = renderHook(() => useHomeData("active"));
      unmount();
      gate.reject?.(new Error("post-unmount load failure"));
    } finally {
      apiModule.api.listWorkspaces = original;
    }
  });

  it("exposes reloadWorkspaces, callable to re-trigger the current tab's fetch", async () => {
    let calls = 0;
    server.use(
      http.get("/api/workspaces", () => {
        calls += 1;
        return HttpResponse.json([]);
      }),
    );
    mockProjects([]);
    const { result } = renderHook(() => useHomeData("active"));
    await waitFor(() => expect(result.current.workspaces).not.toBeNull());
    const callsAfterInitialLoad = calls;
    result.current.reloadWorkspaces();
    await waitFor(() => expect(calls).toBeGreaterThan(callsAfterInitialLoad));
  });
});
