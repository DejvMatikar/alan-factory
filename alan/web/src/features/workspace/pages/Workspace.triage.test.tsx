import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

import { HealthProvider } from "../../../api/HealthProvider";
import { server } from "../../../test/server";
import Workspace from "./Workspace";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

describe("Workspace detail — triage team", () => {
  function renderTriageAt(taskId = "t1") {
    return render(
      <MemoryRouter initialEntries={[`/workspaces/${taskId}`]}>
        <HealthProvider>
          <Routes>
            <Route path="/workspaces/:taskId" element={<Workspace />} />
            <Route path="/" element={<div data-testid="list-page">list</div>} />
          </Routes>
        </HealthProvider>
      </MemoryRouter>,
    );
  }

  function healthWithSweeper(sweeper: {
    timestamp_utc: string | null;
    surface_count: number | null;
    blocked_count: number | null;
  }) {
    return http.get("/api/health", () =>
      HttpResponse.json({
        ok: true,
        queue_paused: false,
        paused_until: null,
        health_state: null,
        sweeper: { next_eligible_utc: null, recent_actions: [], ...sweeper },
      }),
    );
  }

  it("renders the sweeper status card fed by health context and hides the generic invocation body", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, team: "triage" })),
      healthWithSweeper({ timestamp_utc: "2026-07-11T09:00:00Z", surface_count: 5, blocked_count: 2 }),
      noEventsHandler(),
    );
    renderTriageAt();
    expect(await screen.findByRole("heading", { level: 1, name: "T1 Title" })).toBeInTheDocument();
    expect(await screen.findByTestId("triage-sweep-card")).toBeInTheDocument();
    expect(screen.getByTestId("triage-sweep-surface-count")).toHaveTextContent("5");
    expect(screen.getByTestId("triage-sweep-blocked-count")).toHaveTextContent("2");
    expect(screen.queryByTestId("invocation-graph")).not.toBeInTheDocument();
    expect(screen.queryByTestId("invocation-graph-empty")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3, name: "Invocations" })).not.toBeInTheDocument();
  });

  it("never fetches the invocations endpoint for a triage workspace", async () => {
    let invocationsCalls = 0;
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, team: "triage" })),
      http.get("/api/workspaces/t1/invocations", () => {
        invocationsCalls += 1;
        return HttpResponse.json([]);
      }),
      noEventsHandler(),
    );
    renderTriageAt();
    await screen.findByTestId("triage-sweep-card");
    await new Promise((r) => setTimeout(r, 20));
    expect(invocationsCalls).toBe(0);
  });

  it("shows a clean never-run state when the sweeper has not swept yet", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, team: "triage" })),
      noEventsHandler(),
    );
    renderTriageAt();
    expect(await screen.findByTestId("triage-sweep-never-run")).toBeInTheDocument();
  });

  it("distinguishes a real zero surface/blocked count from the never-run dash", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, team: "triage" })),
      healthWithSweeper({ timestamp_utc: "2026-07-11T09:00:00Z", surface_count: 0, blocked_count: 0 }),
      noEventsHandler(),
    );
    renderTriageAt();
    expect(await screen.findByTestId("triage-sweep-surface-count")).toHaveTextContent("0");
    expect(screen.getByTestId("triage-sweep-blocked-count")).toHaveTextContent("0");
    expect(screen.queryByTestId("triage-sweep-never-run")).not.toBeInTheDocument();
  });

  it("still allows archiving a triage workspace from the header", async () => {
    const user = userEvent.setup();
    let archived = false;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, team: "triage", status: archived ? "archived" : "active" }),
      ),
      http.post("/api/workspaces/t1/archive", () => {
        archived = true;
        return new HttpResponse(null, { status: 204 });
      }),
      noEventsHandler(),
    );
    renderTriageAt();
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() => {
      expect(screen.getByTestId("status-archived")).toBeInTheDocument();
    });
  });

  it("fetches invocations and shows the generic body for a non-triage workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("invocation-graph-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("triage-sweep-card")).not.toBeInTheDocument();
  });
});
