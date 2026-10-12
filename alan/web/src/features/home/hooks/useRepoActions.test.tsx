import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { StrictMode, useEffect, useRef, useState, type ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { Project } from "../../../api";
import HiddenRepoDialog, {
  HIDDEN_REPO_DISCLOSURE_SUMMARY_ID,
  HiddenRepoAnnouncementRegion,
} from "../../../components/HiddenRepoDialog";
import { server } from "../../../test/server";
import { hideReportSentence, hideReportTone, useRepoActions } from "./useRepoActions";

function makeWrapper(opts: { strict?: boolean } = {}) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    const tree = (
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="*" element={<>{children}</>} />
        </Routes>
        <HiddenRepoAnnouncementRegion />
      </MemoryRouter>
    );
    return opts.strict ? <StrictMode>{tree}</StrictMode> : tree;
  };
}

function project(id: string): Project {
  return {
    id,
    title: `Project ${id}`,
    status: "active",
    doc_ref: null,
    autonomy: "propose",
    budget_usd: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  };
}

describe("useRepoActions — modal state", () => {
  it("opens and closes the New-project modal, clearing any prior outcome on open", () => {
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    expect(result.current.modalOpen).toBe(false);
    act(() => result.current.openModal());
    expect(result.current.modalOpen).toBe(true);
    act(() => result.current.closeModal());
    expect(result.current.modalOpen).toBe(false);
  });
});

describe("useRepoActions — onProjectCreated bind outcomes", () => {
  it("binds successfully: PATCH carries engine_key equal to the repo key", async () => {
    let patchBody: unknown = null;
    let patchUrl = "";
    server.use(
      http.patch("/api/projects/p1", async ({ request }) => {
        patchUrl = new URL(request.url).pathname;
        patchBody = await request.json();
        return HttpResponse.json({ ...project("p1"), engine_key: "acme/widget" });
      }),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.onProjectCreated(project("p1")));
    await waitFor(() => expect(result.current.bindOutcome).not.toBeNull());
    expect(patchUrl).toBe("/api/projects/p1");
    expect(patchBody).toEqual({ engine_key: "acme/widget" });
    expect(result.current.bindOutcome).toEqual({ kind: "bound", project: project("p1") });
    expect(result.current.modalOpen).toBe(false);
  });

  it("maps a 422 rejection to reason=preflight", async () => {
    server.use(
      http.patch("/api/projects/p2", () =>
        HttpResponse.json({ detail: "engine preflight not passing" }, { status: 422 }),
      ),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.onProjectCreated(project("p2")));
    await waitFor(() => expect(result.current.bindOutcome).not.toBeNull());
    expect(result.current.bindOutcome).toEqual({
      kind: "unbound",
      project: project("p2"),
      reason: "preflight",
      detail: "422: engine preflight not passing",
    });
  });

  it("maps a 409 rejection to reason=live_workspaces", async () => {
    server.use(
      http.patch("/api/projects/p3", () =>
        HttpResponse.json({ detail: "project has live workspaces" }, { status: 409 }),
      ),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.onProjectCreated(project("p3")));
    await waitFor(() => expect(result.current.bindOutcome).not.toBeNull());
    expect(result.current.bindOutcome).toMatchObject({ kind: "unbound", reason: "live_workspaces" });
  });

  it("maps a 400 rejection to reason=unknown_engine", async () => {
    server.use(
      http.patch("/api/projects/p4", () =>
        HttpResponse.json({ detail: "unknown engine: 'acme/widget'" }, { status: 400 }),
      ),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.onProjectCreated(project("p4")));
    await waitFor(() => expect(result.current.bindOutcome).not.toBeNull());
    expect(result.current.bindOutcome).toMatchObject({ kind: "unbound", reason: "unknown_engine" });
  });

  it("maps any other ApiError status to reason=other", async () => {
    server.use(
      http.patch("/api/projects/p5", () =>
        HttpResponse.json({ detail: "boom" }, { status: 503 }),
      ),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.onProjectCreated(project("p5")));
    await waitFor(() => expect(result.current.bindOutcome).not.toBeNull());
    expect(result.current.bindOutcome).toMatchObject({ kind: "unbound", reason: "other" });
  });

  it("maps a non-ApiError rejection to reason=other with String(err) detail", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.updateProject;
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    apiModule.api.updateProject = () => Promise.reject("network-fell-over");
    try {
      const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
      act(() => result.current.onProjectCreated(project("p6")));
      await waitFor(() => expect(result.current.bindOutcome).not.toBeNull());
      expect(result.current.bindOutcome).toEqual({
        kind: "unbound",
        project: project("p6"),
        reason: "other",
        detail: "network-fell-over",
      });
    } finally {
      apiModule.api.updateProject = original;
    }
  });
});

describe("useRepoActions — maintenance dispatch", () => {
  it("dispatches maintenance scoped to the repo key, reports it running, and clears the dispatching flag", async () => {
    let createBody: unknown = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ task_id: "hc-1", root_task_ids: ["hc-1"] }, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.startMaintenance());
    expect(result.current.maintenanceDispatching).toBe(true);
    await waitFor(() => expect(result.current.maintenanceDispatching).toBe(false));
    expect(result.current.maintenanceOutcome).toEqual({ kind: "running", taskId: "hc-1" });
    expect(createBody).toMatchObject({ team: "maintenance", engine_key: "acme/widget" });
  });

  it("surfaces a dispatch failure as an error outcome", async () => {
    server.use(
      http.post("/api/workspaces", () => HttpResponse.json({ detail: "hc-boom" }, { status: 500 })),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.startMaintenance());
    await waitFor(() => expect(result.current.maintenanceOutcome).not.toBeNull());
    const outcome = result.current.maintenanceOutcome;
    if (outcome?.kind !== "error") throw new Error("expected an error outcome");
    expect(outcome.detail).toContain("hc-boom");
  });

  it("repeated runs yield distinct task ids, each overwriting the prior outcome", async () => {
    let callCount = 0;
    server.use(
      http.post("/api/workspaces", () => {
        callCount += 1;
        const taskId = `hc-ws-${String(callCount)}`;
        return HttpResponse.json({ task_id: taskId, root_task_ids: [taskId] }, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.startMaintenance());
    await waitFor(() => expect(result.current.maintenanceOutcome).toEqual({ kind: "running", taskId: "hc-ws-1" }));
    act(() => result.current.startMaintenance());
    await waitFor(() => expect(result.current.maintenanceOutcome).toEqual({ kind: "running", taskId: "hc-ws-2" }));
  });

  it("stopMaintenance rewrites a running outcome to stopped only after the stop endpoint confirms, tracking maintenanceStopping meanwhile", async () => {
    let stoppedTaskId: string | null = null;
    let resolveStop: (() => void) | null = null;
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ task_id: "hc-stop", root_task_ids: ["hc-stop"] }, { status: 201 }),
      ),
      http.post("/api/workspaces/hc-stop/stop", async () => {
        stoppedTaskId = "hc-stop";
        await new Promise<void>((resolve) => {
          resolveStop = resolve;
        });
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.startMaintenance());
    await waitFor(() => expect(result.current.maintenanceOutcome).toEqual({ kind: "running", taskId: "hc-stop" }));
    act(() => result.current.stopMaintenance());
    await waitFor(() => expect(result.current.maintenanceStopping).toBe(true));
    expect(result.current.maintenanceOutcome).toEqual({ kind: "running", taskId: "hc-stop" });
    act(() => resolveStop?.());
    await waitFor(() => expect(result.current.maintenanceOutcome).toEqual({ kind: "stopped", taskId: "hc-stop" }));
    expect(result.current.maintenanceStopping).toBe(false);
    expect(stoppedTaskId).toBe("hc-stop");
  });

  it("stopMaintenance keeps the outcome running and attaches stopError when the stop endpoint itself fails, instead of falsely reporting stopped", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ task_id: "hc-stop-fail", root_task_ids: ["hc-stop-fail"] }, { status: 201 }),
      ),
      http.post("/api/workspaces/hc-stop-fail/stop", () =>
        HttpResponse.json({ detail: "stop-boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.startMaintenance());
    await waitFor(() =>
      expect(result.current.maintenanceOutcome).toEqual({ kind: "running", taskId: "hc-stop-fail" }),
    );
    act(() => result.current.stopMaintenance());
    await waitFor(() => expect(result.current.maintenanceStopping).toBe(false));
    expect(result.current.maintenanceOutcome).toMatchObject({ kind: "running", taskId: "hc-stop-fail" });
    const outcome = result.current.maintenanceOutcome;
    if (outcome?.kind !== "running") throw new Error("expected a running outcome");
    expect(outcome.stopError).toContain("stop-boom");
  });

  it("a second stopMaintenance call while one is already in flight does not issue a second stop request", async () => {
    let stopCalls = 0;
    let resolveStop: (() => void) | null = null;
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ task_id: "hc-stop-guard", root_task_ids: ["hc-stop-guard"] }, { status: 201 }),
      ),
      http.post("/api/workspaces/hc-stop-guard/stop", async () => {
        stopCalls += 1;
        await new Promise<void>((resolve) => {
          resolveStop = resolve;
        });
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.startMaintenance());
    await waitFor(() =>
      expect(result.current.maintenanceOutcome).toEqual({ kind: "running", taskId: "hc-stop-guard" }),
    );
    act(() => result.current.stopMaintenance());
    act(() => result.current.stopMaintenance());
    await waitFor(() => expect(result.current.maintenanceStopping).toBe(true));
    act(() => resolveStop?.());
    await waitFor(() =>
      expect(result.current.maintenanceOutcome).toEqual({ kind: "stopped", taskId: "hc-stop-guard" }),
    );
    expect(stopCalls).toBe(1);
  });

  it("fires the stop POST exactly once per click even under React.StrictMode's double-invoke of setState updaters", async () => {
    let stopCalls = 0;
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ task_id: "hc-strict", root_task_ids: ["hc-strict"] }, { status: 201 }),
      ),
      http.post("/api/workspaces/hc-strict/stop", () => {
        stopCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), {
      wrapper: makeWrapper({ strict: true }),
    });
    act(() => result.current.startMaintenance());
    await waitFor(() =>
      expect(result.current.maintenanceOutcome).toEqual({ kind: "running", taskId: "hc-strict" }),
    );
    act(() => result.current.stopMaintenance());
    await waitFor(() =>
      expect(result.current.maintenanceOutcome).toEqual({ kind: "stopped", taskId: "hc-strict" }),
    );
    expect(stopCalls).toBe(1);
  });

  it("stopMaintenance is a no-op when there is no running outcome yet", () => {
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.stopMaintenance());
    expect(result.current.maintenanceOutcome).toBeNull();
  });

  it("stopMaintenance is a no-op once the outcome is already an error", async () => {
    server.use(
      http.post("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.startMaintenance());
    await waitFor(() => expect(result.current.maintenanceOutcome).not.toBeNull());
    act(() => result.current.stopMaintenance());
    expect(result.current.maintenanceOutcome).toMatchObject({ kind: "error" });
  });
});

describe("hideReportSentence / hideReportTone", () => {
  const base = {
    slug: "acme/widget",
    residue_workspace_ids: [] as string[],
    workers_interrupted: 0,
    chats_cancelled: 0,
    pool_disposed: false,
    pool_residue: null as string | null,
  };

  it("falls back to a generic pool message and a warn tone when the pool refuses to dispose without naming a reason", () => {
    expect(hideReportSentence(base)).toContain("its warm pool not fully torn down");
    expect(hideReportTone(base)).toBe("warn");
  });

  it("honestly names residual workspaces (plural) and an undisposed pool instead of reporting a false-clean hide", () => {
    const report = {
      ...base,
      residue_workspace_ids: ["w1", "w2"],
      pool_residue: "pool busy: 1 lease outstanding",
    };
    const sentence = hideReportSentence(report);
    expect(sentence).toContain("2 workspaces still running");
    expect(sentence).toContain("pool busy: 1 lease outstanding");
    expect(hideReportTone(report)).toBe("warn");
  });
});

describe("useRepoActions — hide", () => {
  const report = {
    slug: "acme/widget",
    residue_workspace_ids: [],
    workers_interrupted: 2,
    chats_cancelled: 1,
    pool_disposed: true,
    pool_residue: null,
  };

  it("hides the repo, tracks the busy flag, and announces the quiesce report through the durable channel", async () => {
    server.use(http.post("/api/engines/acme/widget/hide", () => HttpResponse.json(report)));
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.hideRepo());
    expect(result.current.hiding).toBe(true);
    await waitFor(() => expect(result.current.hiding).toBe(false));
    const announcement = screen.getByTestId("hidden-repo-announcement");
    expect(announcement).toHaveTextContent("stopped cleanly");
    expect(announcement.querySelector("span")).toHaveClass("text-text-primary");
  });

  it("surfaces a hide failure through the announcement channel (the previously-silent catch branch) and clears hiding", async () => {
    server.use(
      http.post("/api/engines/acme/widget/hide", () =>
        HttpResponse.json({ detail: "hide-boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.hideRepo());
    await waitFor(() => expect(result.current.hiding).toBe(false));
    const announcement = screen.getByTestId("hidden-repo-announcement");
    expect(announcement).toHaveTextContent("Hide failed — 500: hide-boom");
    expect(announcement.querySelector("span")).toHaveClass("font-medium", "text-btn-destroy");
  });

  it("moves focus to the hidden-repos disclosure trigger once a hide succeeds", async () => {
    server.use(http.post("/api/engines/acme/widget/hide", () => HttpResponse.json(report)));
    const trigger = document.createElement("button");
    trigger.id = HIDDEN_REPO_DISCLOSURE_SUMMARY_ID;
    document.body.appendChild(trigger);
    try {
      const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
      act(() => result.current.hideRepo());
      await waitFor(() => expect(document.activeElement).toBe(trigger));
    } finally {
      trigger.remove();
    }
  });

  it("leaves focus alone when the hide call fails", async () => {
    server.use(
      http.post("/api/engines/acme/widget/hide", () =>
        HttpResponse.json({ detail: "hide-boom" }, { status: 500 }),
      ),
    );
    const trigger = document.createElement("button");
    trigger.id = HIDDEN_REPO_DISCLOSURE_SUMMARY_ID;
    document.body.appendChild(trigger);
    try {
      const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
      act(() => result.current.hideRepo());
      await waitFor(() => expect(result.current.hiding).toBe(false));
      expect(document.activeElement).not.toBe(trigger);
    } finally {
      trigger.remove();
    }
  });

  it("repeated hideRepo calls each announce their own report rather than a stuck prior one", async () => {
    let call = 0;
    server.use(
      http.post("/api/engines/acme/widget/hide", () => {
        call += 1;
        return HttpResponse.json({ ...report, residue_workspace_ids: call === 1 ? [] : ["w1"] });
      }),
    );
    const { result } = renderHook(() => useRepoActions("acme/widget"), { wrapper: makeWrapper() });
    act(() => result.current.hideRepo());
    await waitFor(() => expect(result.current.hiding).toBe(false));
    expect(screen.getByTestId("hidden-repo-announcement")).toHaveTextContent("stopped cleanly");
    act(() => result.current.hideRepo());
    await waitFor(() => expect(result.current.hiding).toBe(false));
    expect(screen.getByTestId("hidden-repo-announcement")).toHaveTextContent(
      "1 workspace still running",
    );
  });

  function RowStandIn({ onHidden }: { readonly onHidden: () => void }) {
    const { hideRepo, hiding } = useRepoActions("acme/widget");
    const wasHiding = useRef(false);
    useEffect(() => {
      if (wasHiding.current && !hiding) onHidden();
      wasHiding.current = hiding;
    }, [hiding, onHidden]);
    return (
      <button type="button" data-testid="row-stand-in-hide" onClick={hideRepo}>
        Hide
      </button>
    );
  }

  function HideThenUnmountHarness() {
    const [rowVisible, setRowVisible] = useState(true);
    return (
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <HiddenRepoDialog hiddenEngines={[]} hiddenCount={0} />
                <HiddenRepoAnnouncementRegion />
                {rowVisible && <RowStandIn onHidden={() => setRowVisible(false)} />}
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    );
  }

  it("the announcement survives the repo's own row leaving the visible list, not just rendering momentarily", async () => {
    server.use(http.post("/api/engines/acme/widget/hide", () => HttpResponse.json(report)));
    render(<HideThenUnmountHarness />);
    act(() => {
      screen.getByTestId("row-stand-in-hide").click();
    });
    await waitFor(() => {
      expect(screen.queryByTestId("row-stand-in-hide")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("hidden-repo-announcement")).toHaveTextContent(
      "everything for this repository stopped cleanly",
    );
  });
});
