import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
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

import { server } from "../../../test/server";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

describe("Workspace detail", () => {
  it("shows workspace-total-cost from the workspace detail's total_cost_usd", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, total_cost_usd: 1.5 }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    const badge = await screen.findByTestId("workspace-total-cost");
    expect(badge).toHaveTextContent("Total: $1.50");
  });

  it("hides the whole cost/lead-time panel when neither total cost nor lead time is available", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("workspace-total-cost")).not.toBeInTheDocument();
    expect(screen.queryByTestId("workspace-lead-time")).not.toBeInTheDocument();
  });

  it("shows an explicit empty-state lead time alongside cost when lead_seconds is not yet recorded", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, total_cost_usd: 1.5 }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    const leadEl = await screen.findByTestId("workspace-lead-time");
    expect(leadEl).toHaveTextContent("Lead: —");
    expect(leadEl.getAttribute("aria-label")).toContain("Lead time not yet recorded");
  });

  it("renders the recorded lead time next to the total cost, with an aria-label stating the precise value", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, total_cost_usd: 1.5, lead_seconds: 7800 }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    const leadEl = await screen.findByTestId("workspace-lead-time");
    expect(leadEl).toHaveTextContent("Lead: 2h 10m");
    expect(leadEl.getAttribute("aria-label")).toContain("Lead time 2h 10m");
    expect(screen.getByTestId("workspace-total-cost")).toHaveTextContent("Total: $1.50");
  });

  it("shows the recorded lead time even when total_cost_usd is null, since lead time doesn't depend on cost data", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, total_cost_usd: null, lead_seconds: 3600 }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    const leadEl = await screen.findByTestId("workspace-lead-time");
    expect(leadEl).toHaveTextContent("Lead: 1h 0m");
    expect(screen.queryByTestId("workspace-total-cost")).not.toBeInTheDocument();
  });

  it("hides the cost breakdown disclosure when total_cost_usd is null even if cost_breakdown carries entries", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          total_cost_usd: null,
          lead_seconds: 3600,
          cost_breakdown: [
            {
              key: "team_runs",
              label: "Team & agent runs",
              run_summary: { model: "aggregate", effort: null, system_prompt: "", total_cost_usd: 0.002 },
            },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("workspace-lead-time");
    expect(screen.queryByTestId("workspace-cost-breakdown")).not.toBeInTheDocument();
  });

  it("keeps workspace-total-cost pinned to total_cost_usd when a node_completed SSE event carries different cost data", async () => {
    const taskJson = JSON.stringify({
      id: "orchestrator",
      type: "io.alan.LLMStep",
      role: "orchestrator",
      model: "opus",
      depends_on: [],
      status: "success",
      started_at: "2026-05-12T08:00:00Z",
      completed_at: "2026-05-12T08:00:30Z",
      stream_path: "agents/orchestrator.stream.jsonl",
      dispatched: [],
      error: null,
      input_tokens: 100,
      output_tokens: 50,
      cache_creation_input_tokens: null,
      cache_read_input_tokens: null,
      total_cost_usd: 0.0020,
      usage_raw: null,
    });
    const payload = JSON.stringify({
      inv_id: "inv_001",
      role: "orchestrator",
      node: taskJson,
    });
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, total_cost_usd: 1.5 }),
      ),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "research",
          status: "running",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: null,
          tasks: [
            {
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
              input_tokens: null,
              output_tokens: null,
              cache_creation_input_tokens: null,
              cache_read_input_tokens: null,
              total_cost_usd: null,
              usage_raw: null,
            },
          ],
        }),
      ),
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            `event: node_completed\ndata: ${payload}\n\n`,
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    renderAt();
    const badge = await screen.findByTestId("workspace-total-cost");
    expect(badge).toHaveTextContent("Total: $1.50");
    const invocationCost = await screen.findByTestId("run-row-cost-inv_001");
    await waitFor(() => {
      expect(invocationCost).toHaveTextContent("$0.00");
    });
    expect(screen.getByTestId("workspace-total-cost")).toHaveTextContent("Total: $1.50");
  });

  it("shows the cost breakdown disclosure, collapsed by default, with category costs revealed on expand", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          total_cost_usd: 0.0025,
          cost_breakdown: [
            {
              key: "team_runs",
              label: "Team & agent runs",
              run_summary: { model: "aggregate", effort: null, system_prompt: "", total_cost_usd: 0.002 },
            },
            {
              key: "status_summary",
              label: "Status summary",
              run_summary: null,
            },
            {
              key: "discussion",
              label: "Discussion chat",
              run_summary: { model: "aggregate", effort: null, system_prompt: "", total_cost_usd: 0.0005 },
            },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("workspace-total-cost");
    const disclosure = screen.getByTestId("workspace-cost-breakdown");
    expect(screen.getByTestId("run-summary-header-team_runs")).not.toBeVisible();

    await user.click(screen.getByText("Cost breakdown"));

    expect(disclosure).toHaveAttribute("open");
    expect(screen.getByTestId("run-summary-header-team_runs")).toBeVisible();
    expect(screen.getByTestId("run-summary-setup-team_runs")).toHaveTextContent(
      "Team & agent runs",
    );
    expect(screen.getByTestId("run-summary-cost-team_runs")).toHaveTextContent("$0.0020");
    expect(screen.getByTestId("run-summary-setup-discussion")).toHaveTextContent(
      "Discussion chat",
    );
    expect(screen.getByTestId("run-summary-cost-discussion")).toHaveTextContent("$0.0005");
    expect(screen.queryByTestId("run-summary-header-status_summary")).not.toBeInTheDocument();
  });

  it("opens the cost breakdown disclosure via keyboard activation on the summary", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          total_cost_usd: 0.0025,
          cost_breakdown: [
            {
              key: "team_runs",
              label: "Team & agent runs",
              run_summary: { model: "aggregate", effort: null, system_prompt: "", total_cost_usd: 0.0025 },
            },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    const summary = (await screen.findByText("Cost breakdown")).closest("summary");
    if (summary === null) throw new Error("summary element not found");
    const row = screen.getByTestId("run-summary-header-team_runs");
    expect(row).not.toBeVisible();

    fireEvent.keyDown(summary, { key: "Enter" });
    expect(row).toBeVisible();

    fireEvent.keyDown(summary, { key: " " });
    expect(row).not.toBeVisible();
  });

  it("omits the cost breakdown disclosure when every category is null", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          total_cost_usd: 0,
          cost_breakdown: [
            { key: "team_runs", label: "Team & agent runs", run_summary: null },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("workspace-total-cost");
    expect(screen.queryByTestId("workspace-cost-breakdown")).not.toBeInTheDocument();
  });
});

describe("ManagerSummaryPanel", () => {
  it("renders the panel with text when summary.ok is true", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "completed",
          manager_summary: { ok: true, text: "The task completed successfully." },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const panel = await screen.findByTestId("manager-summary-panel");
    expect(panel).toBeInTheDocument();
    expect(screen.getByText("The task completed successfully.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "What happened" })).toBeInTheDocument();
  });

  it("shows unavailable indicator when summary.ok is false", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "awaiting_input",
          manager_summary: { ok: false, text: null },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("manager-summary-unavailable");
    expect(screen.queryByTestId("manager-summary-panel")).toBeNull();
  });

  it("shows unavailable indicator when ok is true but text is null", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          manager_summary: { ok: true, text: null },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("manager-summary-unavailable");
  });

  it("omits the panel when manager_summary is null", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, manager_summary: null }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("manager-summary-panel")).toBeNull();
    expect(screen.queryByTestId("manager-summary-unavailable")).toBeNull();
  });

  it("omits the panel when manager_summary field is absent", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("manager-summary-panel")).toBeNull();
    expect(screen.queryByTestId("manager-summary-unavailable")).toBeNull();
  });

  it("panel appears before invocations section in the DOM", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          manager_summary: { ok: true, text: "Summary text." },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("manager-summary-panel");
    const panel = screen.getByTestId("manager-summary-panel");
    const invHeading = screen.getByRole("heading", { level: 3, name: "Invocations" });
    expect(
      panel.compareDocumentPosition(invHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("no longer shows the summarizer's own cost beside the what-happened text (it moved to the cost breakdown)", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          manager_summary: {
            ok: true,
            text: "The task completed successfully.",
            run_summary: {
              model: "haiku",
              effort: null,
              system_prompt: "You are a concise status reporter.",
              total_cost_usd: 0.0003,
              total_output_tokens: 120,
              total_input_tokens: 900,
              tool_counts: [],
            },
          },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const panel = await screen.findByTestId("manager-summary-panel");
    expect(within(panel).getByText("The task completed successfully.")).toBeInTheDocument();
    expect(within(panel).queryByTestId("run-summary-header")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("run-summary-cost")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("run-summary-prompt-toggle")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("run-summary-prompt-distilled")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("run-summary-prompt-full")).not.toBeInTheDocument();
  });

  it("shows the panel with no pricing row when the manager-summary usage is missing", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          manager_summary: { ok: true, text: "The task completed successfully." },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const panel = await screen.findByTestId("manager-summary-panel");
    expect(within(panel).getByText("The task completed successfully.")).toBeInTheDocument();
    expect(within(panel).queryByTestId("run-summary-header")).not.toBeInTheDocument();
  });
});
