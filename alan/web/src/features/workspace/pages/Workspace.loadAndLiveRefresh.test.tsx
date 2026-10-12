import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
  }: {
    nodes: { id: string; type: string; data: Record<string, unknown> }[];
    nodeTypes: Record<
      string,
      React.ComponentType<{ data: Record<string, unknown> }>
    >;
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

import type { WorkspaceDetail } from "../../../api";
import { server } from "../../../test/server";
import Workspace from "./Workspace";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

describe("Workspace detail", () => {
  it("shows the load error when getWorkspace fails", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("404");
  });

  it("renders nothing when taskId is missing", () => {
    render(
      <MemoryRouter initialEntries={["/workspaces/"]}>
        <Routes>
          <Route path="/workspaces/:taskId?" element={<Workspace />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("missing-task")).toBeInTheDocument();
  });

  it("refreshes when a job_completed SSE event arrives", async () => {
    let getCalls = 0;
    server.use(
      http.get("/api/workspaces/t1", () => {
        getCalls += 1;
        return HttpResponse.json(BASE);
      }),
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: job_completed\ndata: {"team":"research","job_id":"7","handover_path":"x"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    await waitFor(() => {
      expect(getCalls).toBeGreaterThanOrEqual(2);
    });
  });

  it("refreshes when a job_retry_scheduled SSE event arrives", async () => {
    let getCalls = 0;
    server.use(
      http.get("/api/workspaces/t1", () => {
        getCalls += 1;
        return HttpResponse.json(BASE);
      }),
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: job_retry_scheduled\ndata: {"team":"research","job_id":"5","attempt":"1","eligible_at":"9999999999.0"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    await waitFor(() => {
      expect(getCalls).toBeGreaterThanOrEqual(2);
    });
  });

  it("only triggers one refetch when the same inv_id appears twice", async () => {
    let getCalls = 0;
    const nodeJson = JSON.stringify({
      id: "n",
      type: "io.alan.LLMStep",
      role: "n",
      model: "opus",
      depends_on: [],
      status: "running",
      started_at: "2026-05-12T08:00:00Z",
      completed_at: null,
      stream_path: null,
      dispatched: [],
    error: null,
    });
    const payload = JSON.stringify({
      inv_id: "inv_001",
      role: "n",
      node: nodeJson,
    });
    server.use(
      http.get("/api/workspaces/t1", () => {
        getCalls += 1;
        return HttpResponse.json(BASE);
      }),
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            `event: node_started\ndata: ${payload}\n\nevent: node_completed\ndata: ${payload}\n\n`,
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    await waitFor(() => {
      expect(getCalls).toBeGreaterThanOrEqual(2);
    });
    await new Promise((r) => setTimeout(r, 50));
    expect(getCalls).toBe(2);
  });

  it("re-fetches when a node_started event names a new invocation", async () => {
    let getCalls = 0;
    const nodeJson = JSON.stringify({
      id: "orchestrator",
      type: "io.alan.LLMStep",
      role: "orchestrator",
      model: "opus",
      depends_on: [],
      status: "running",
      started_at: "2026-05-12T08:00:00Z",
      completed_at: null,
      stream_path: "agents/orchestrator.stream.jsonl",
      dispatched: [],
    error: null,
    });
    const payload = JSON.stringify({
      inv_id: "inv_001",
      role: "orchestrator",
      node: nodeJson,
    });
    server.use(
      http.get("/api/workspaces/t1", () => {
        getCalls += 1;
        return HttpResponse.json({ ...BASE, title: `seen-${getCalls}` });
      }),
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(`event: node_started\ndata: ${payload}\n\n`, {
            headers: { "Content-Type": "text/event-stream" },
          }),
      ),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: /seen-/ });
    await waitFor(() => {
      expect(getCalls).toBeGreaterThanOrEqual(2);
    });
  });

  it("surfaces non-Error throws via String(err) on load failure", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.getWorkspace;
    apiModule.api.getWorkspace = () => {
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      return Promise.reject("string-load-error");
    };
    try {
      server.use(noEventsHandler());
      renderAt();
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "string-load-error",
      );
    } finally {
      apiModule.api.getWorkspace = original;
    }
  });

  it("surfaces an SSE stream-open failure as a non-blocking warning", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/events", () =>
        HttpResponse.json({ detail: "no such workspace" }, { status: 404 }),
      ),
    );
    renderAt();
    expect(await screen.findByTestId("stream-warning")).toHaveTextContent(
      "Live updates unavailable",
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "T1 Title" }),
    ).toBeInTheDocument();
  });

  it("ignores stale getWorkspace rejections (race guard)", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.getWorkspace;
    const rejecter: { fn: ((err: unknown) => void) | null } = { fn: null };
    let callIndex = 0;
    apiModule.api.getWorkspace = () => {
      callIndex += 1;
      if (callIndex === 1) {
        return new Promise<WorkspaceDetail>((_resolve, reject) => {
          rejecter.fn = reject;
        });
      }
      return Promise.resolve(BASE);
    };
    const nodeJson = JSON.stringify({
      id: "x",
      type: "io.alan.LLMStep",
      role: "x",
      model: "opus",
      depends_on: [],
      status: "running",
      started_at: "2026-05-12T08:00:00Z",
      completed_at: null,
      stream_path: null,
      dispatched: [],
    error: null,
    });
    const payload = JSON.stringify({
      inv_id: "inv_001",
      role: "x",
      node: nodeJson,
    });
    server.use(
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(`event: node_started\ndata: ${payload}\n\n`, {
            headers: { "Content-Type": "text/event-stream" },
          }),
      ),
    );
    try {
      renderAt();
      await screen.findByRole("heading", { level: 1, name: "T1 Title" });
      rejecter.fn?.(new Error("stale error"));
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(
        screen.getByRole("heading", { level: 1, name: "T1 Title" }),
      ).toBeInTheDocument();
    } finally {
      apiModule.api.getWorkspace = original;
    }
  });

  it("passes ?include=triage to getWorkspace so triage detail pages load", async () => {
    let capturedInclude = "";
    server.use(
      http.get("/api/workspaces/t1", ({ request }) => {
        capturedInclude = new URL(request.url).searchParams.get("include") ?? "";
        return HttpResponse.json(BASE);
      }),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(capturedInclude).toBe("triage");
  });
});
