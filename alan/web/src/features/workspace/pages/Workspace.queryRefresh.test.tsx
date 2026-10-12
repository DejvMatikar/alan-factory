import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
  }: {
    nodes: { id: string; type: string; data: Record<string, unknown> }[];
    nodeTypes: Record<string, React.ComponentType<{ data: Record<string, unknown> }>>;
  }) {
    return React.createElement(
      "div",
      { "data-testid": "rf-mock" },
      nodes.map((n) => {
        const Component = nodeTypes[n.type]!;
        return React.createElement(Component, { key: n.id, data: n.data });
      }),
    );
  }
  function Stub() {
    return null;
  }
  return { ReactFlow, Background: Stub, Controls: Stub };
});

import { server } from "../../../test/server";
import { makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import Workspace from "./Workspace";
import { installWorkspaceDefaultHandlers } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

const RETRY_FRAME =
  'event: job_retry_scheduled\ndata: {"team":"research","job_id":"5","attempt":"1","eligible_at":"9999999999.0"}\n\n';

function invocationDoc(invId: string) {
  return {
    schema_version: "1.0",
    id: invId,
    namespace: "alan.workspace.t1",
    team: "research",
    status: "success",
    started_at: "2026-05-12T08:00:00Z",
    completed_at: "2026-05-12T08:00:30Z",
    tasks: [],
  };
}

function gatedEvents(taskId: string) {
  const gate: { release: (() => void) | null } = { release: null };
  const handler = http.get(
    `/api/workspaces/${taskId}/events`,
    () =>
      new Promise<Response>((resolve) => {
        gate.release = () =>
          resolve(new HttpResponse(RETRY_FRAME, { headers: { "Content-Type": "text/event-stream" } }));
      }),
  );
  return { gate, handler };
}

function renderWorkspaceRoutes(initialTaskId = "t1") {
  return render(
    <MemoryRouter initialEntries={[`/workspaces/${initialTaskId}`]}>
      <Link to="/workspaces/t2" data-testid="go-t2">
        t2
      </Link>
      <Routes>
        <Route path="/workspaces/:taskId" element={<Workspace />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Workspace query-backed refresh", () => {
  it("shows loading instead of the previous task when the task id changes", async () => {
    const second: { release: (() => void) | null } = { release: null };
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(makeWorkspaceDetail({ task_id: "t1", title: "First Title" })),
      ),
      http.get("/api/workspaces/t2", () =>
        new Promise<Response>((resolve) => {
          second.release = () =>
            resolve(HttpResponse.json(makeWorkspaceDetail({ task_id: "t2", title: "Second Title" })));
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      http.get("/api/workspaces/t2/invocations", () => HttpResponse.json([])),
      http.get("/api/workspaces/t1/events", () => HttpResponse.error()),
      http.get("/api/workspaces/t2/events", () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    renderWorkspaceRoutes();
    await screen.findByRole("heading", { level: 1, name: "First Title" });

    await user.click(screen.getByTestId("go-t2"));
    expect(await screen.findByTestId("loading")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "First Title" })).not.toBeInTheDocument();

    await waitFor(() => expect(second.release).not.toBeNull());
    second.release!();
    expect(await screen.findByRole("heading", { level: 1, name: "Second Title" })).toBeInTheDocument();
  });

  it("replaces the page with the load alert when a background refresh fails", async () => {
    const { gate, handler } = gatedEvents("t1");
    let workspaceCalls = 0;
    server.use(
      http.get("/api/workspaces/t1", () => {
        workspaceCalls += 1;
        if (workspaceCalls > 1) return HttpResponse.json({ detail: "gone" }, { status: 500 });
        return HttpResponse.json(makeWorkspaceDetail({ title: "T1 Title" }));
      }),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      handler,
    );
    render(
      <MemoryRouter initialEntries={["/workspaces/t1"]}>
        <Routes>
          <Route path="/workspaces/:taskId" element={<Workspace />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    await waitFor(() => expect(gate.release).not.toBeNull());
    gate.release!();
    expect(await screen.findByRole("alert")).toHaveTextContent("500");
    expect(screen.queryByRole("heading", { level: 1, name: "T1 Title" })).not.toBeInTheDocument();
  });

  it("surfaces an invocation-list failure through the same load alert", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(makeWorkspaceDetail({ title: "T1 Title" }))),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json({ detail: "list broke" }, { status: 503 }),
      ),
      http.get("/api/workspaces/t1/events", () => HttpResponse.error()),
    );
    render(
      <MemoryRouter initialEntries={["/workspaces/t1"]}>
        <Routes>
          <Route path="/workspaces/:taskId" element={<Workspace />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("503");
  });

  it("empties the invocation list on each live refresh and refetches its documents", async () => {
    const { gate, handler } = gatedEvents("t1");
    let listCalls = 0;
    let releaseSecondList: () => void = () => undefined;
    const docRequests: string[] = [];
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(makeWorkspaceDetail({ title: "T1 Title" }))),
      http.get("/api/workspaces/t1/invocations", () => {
        listCalls += 1;
        if (listCalls === 1) return HttpResponse.json(["inv_001"]);
        return new Promise<Response>((resolve) => {
          releaseSecondList = () => resolve(HttpResponse.json(["inv_001"]));
        });
      }),
      http.get("/api/workspaces/t1/invocations/inv_001", () => {
        docRequests.push("inv_001");
        return HttpResponse.json(invocationDoc("inv_001"));
      }),
      handler,
    );
    render(
      <MemoryRouter initialEntries={["/workspaces/t1"]}>
        <Routes>
          <Route path="/workspaces/:taskId" element={<Workspace />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId("run-row-inv_001");
    await waitFor(() => expect(docRequests).toHaveLength(1));
    await waitFor(() => expect(gate.release).not.toBeNull());

    gate.release!();
    expect(await screen.findByTestId("invocation-graph-empty")).toBeInTheDocument();
    await waitFor(() => expect(listCalls).toBe(2));
    releaseSecondList();
    await screen.findByTestId("run-row-inv_001");
    await waitFor(() => expect(docRequests).toHaveLength(2));
  });

  it("never requests the invocations of a triage workspace, refreshes included", async () => {
    const user = userEvent.setup();
    let invocationCalls = 0;
    let workspaceCalls = 0;
    server.use(
      http.get("/api/workspaces/t1", () => {
        workspaceCalls += 1;
        return HttpResponse.json(makeWorkspaceDetail({ title: "T1 Title", team: "triage" }));
      }),
      http.get("/api/workspaces/t1/invocations", () => {
        invocationCalls += 1;
        return HttpResponse.json([]);
      }),
      http.post("/api/workspaces/t1/archive", () => new HttpResponse(null, { status: 204 })),
    );
    render(
      <MemoryRouter initialEntries={["/workspaces/t1"]}>
        <Routes>
          <Route path="/workspaces/:taskId" element={<Workspace />} />
        </Routes>
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() => expect(workspaceCalls).toBe(2));
    expect(invocationCalls).toBe(0);
  });
});
