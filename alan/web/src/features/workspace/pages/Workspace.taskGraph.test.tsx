import { screen, waitFor, within } from "@testing-library/react";
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

import {
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { server } from "../../../test/server";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

describe("Workspace detail", () => {
  it("shows task-graph section with InterTaskDAG when tasks.length > 1", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "alpha", depends_on: [], team: "implementation_plan", status: "ready" },
            { id: "beta", depends_on: ["alpha"], team: null, status: "pending" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("inter-task-dag")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Task graph" })).toBeInTheDocument();
  });

  it("passes each task's status through to the task-graph's status indicator", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "alpha", depends_on: [], team: "implementation_plan", status: "completed" },
            { id: "beta", depends_on: ["alpha"], team: null, status: "failed" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("dag-status-alpha")).toHaveAttribute(
      "aria-label",
      "status: done",
    );
    expect(screen.getByTestId("dag-status-beta")).toHaveAttribute(
      "aria-label",
      "status: failed",
    );
  });

  it("uses task.title as InterTaskDAG label when available", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "alpha", depends_on: [], team: "implementation_plan", status: "completed", title: "Backend API" },
            { id: "beta", depends_on: ["alpha"], team: "implementation", status: "running", title: null },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("dag-node-alpha")).toHaveTextContent("Backend API");
    expect(screen.getByTestId("dag-node-beta")).toHaveTextContent("implementation");
  });

  it("renders per-task TaskInvocationSection when tasks.length > 1", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "alpha", depends_on: [], team: "implementation_plan", status: "running" },
            { id: "beta", depends_on: ["alpha"], team: null, status: "pending" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 3, name: "Invocations" });
    expect(await screen.findByTestId("task-section-alpha")).toBeInTheDocument();
    expect(screen.getByTestId("task-section-beta")).toBeInTheDocument();
  });

  it("routes invocations to matching task section by source_task_id", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "alpha", depends_on: [], team: "implementation_plan", status: "running" },
            { id: "beta", depends_on: ["alpha"], team: null, status: "pending" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.5",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "implementation_plan",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          source_task_id: "alpha",
          tasks: [],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const alphaSection = await screen.findByTestId("task-section-alpha");
    await within(alphaSection).findByTestId("run-row-inv_001");
    const betaSection = screen.getByTestId("task-section-beta");
    expect(betaSection.querySelector("[data-testid='run-row-inv_001']")).toBeNull();
  });

  it("renders flat InvocationGraph when tasks.length <= 1", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "research",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("run-row-inv_001");
    expect(screen.queryByTestId("task-section-t1")).toBeNull();
  });

  it("does NOT show task-graph section when tasks.length <= 1", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("inter-task-dag")).toBeNull();
    expect(screen.queryByRole("heading", { level: 3, name: "Task graph" })).toBeNull();
  });

  it("shows grouped-invocations sections for multi-task workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "completed", title: "Backend API" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "running", title: "Frontend" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001", "inv_002"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.5",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "implementation",
          source_task_id: "task-a",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/invocations/inv_002", () =>
        HttpResponse.json({
          schema_version: "1.5",
          id: "inv_002",
          namespace: "alan.workspace.t1",
          team: "verification",
          source_task_id: "task-b",
          status: "running",
          started_at: "2026-05-12T08:01:00Z",
          completed_at: null,
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("grouped-invocations")).toBeInTheDocument();
    expect(screen.getByTestId("task-section-task-a")).toBeInTheDocument();
    expect(screen.getByTestId("task-section-heading-task-a")).toHaveTextContent("Backend API");
    await waitFor(() => {
      expect(screen.getByTestId("task-section-run-count-task-a")).toHaveTextContent("1 run");
    });
    expect(screen.getByTestId("task-section-task-b")).toBeInTheDocument();
    expect(screen.getByTestId("task-section-heading-task-b")).toHaveTextContent("Frontend");
  });

  it("falls back to task.id as section heading when task has parent_task_id and no title", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "root-task", depends_on: [], team: null, status: "completed", title: "Root" },
            { id: "child-task", depends_on: ["root-task"], team: "implementation", status: "running", parent_task_id: "root-task" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("task-section-heading-child-task")).toHaveTextContent("child-task");
  });

  it("falls back to ws.title as section heading for root task with no title", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "root-task", depends_on: [], team: null, status: "running" },
            { id: "child-task", depends_on: ["root-task"], team: "implementation", status: "pending", title: "Subtask" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("task-section-heading-root-task")).toHaveTextContent("T1 Title");
  });

  it("keeps single-task workspace flat without grouped-invocations chrome", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("grouped-invocations")).not.toBeInTheDocument();
  });

  it("shows Other-runs section for invocations without source_task_id in multi-task workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "running", title: "Task A" },
            { id: "task-b", depends_on: [], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
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
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("task-section-ungrouped")).toBeInTheDocument();
  });

  it("Plan & decompose collapsible is labelled and appears before per-task sections", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "running", title: "Task A" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_plan"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_plan", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_plan",
          namespace: "alan.workspace.t1",
          team: "implementation_plan",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const ungrouped = await screen.findByTestId("task-section-ungrouped");
    expect(within(ungrouped).getByText("Plan & decompose")).toBeInTheDocument();
    expect(within(ungrouped).getByText("1 run")).toBeInTheDocument();
    const taskA = screen.getByTestId("task-section-task-a");
    expect(
      ungrouped.compareDocumentPosition(taskA) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows usage and cost totals in the Plan & decompose group header, summed across its invocations", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "running", title: "Task A" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001", "inv_002"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "implementation_plan",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [
            {
              id: "n1",
              type: "io.alan.LLMStep",
              role: "n1",
              model: "opus",
              depends_on: [],
              status: "success",
              started_at: null,
              completed_at: null,
              stream_path: null,
              dispatched: [],
              error: null,
              input_tokens: 100,
              output_tokens: 50,
              cache_creation_input_tokens: null,
              cache_read_input_tokens: null,
              total_cost_usd: 0.001,
              usage_raw: null,
            },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations/inv_002", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_002",
          namespace: "alan.workspace.t1",
          team: "decomposer",
          status: "success",
          started_at: "2026-05-12T08:01:00Z",
          completed_at: "2026-05-12T08:01:30Z",
          tasks: [
            {
              id: "n2",
              type: "io.alan.LLMStep",
              role: "n2",
              model: "opus",
              depends_on: [],
              status: "success",
              started_at: null,
              completed_at: null,
              stream_path: null,
              dispatched: [],
              error: null,
              input_tokens: 200,
              output_tokens: 80,
              cache_creation_input_tokens: null,
              cache_read_input_tokens: null,
              total_cost_usd: 0.002,
              usage_raw: null,
            },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await waitFor(() => {
      expect(screen.getByTestId("task-section-cost-ungrouped")).toHaveTextContent(
        "300↑ 130↓ · $0.0030",
      );
    });
  });

  it("omits the cost summary in the Plan & decompose header when no usage is recorded", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "running", title: "Task A" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json(["inv_001"])),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "implementation_plan",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("task-section-ungrouped");
    await waitFor(() => {
      expect(screen.getByTestId("task-section-status-ungrouped")).toHaveTextContent("done");
    });
    expect(screen.queryByTestId("task-section-cost-ungrouped")).not.toBeInTheDocument();
  });

  it("shows a done aggregate status in the Plan & decompose header when every invocation succeeded", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "completed", title: "Task A" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "completed", title: "Task B" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json(["inv_001"])),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "implementation_plan",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await waitFor(() => {
      expect(screen.getByTestId("task-section-status-ungrouped")).toHaveTextContent("done");
    });
  });

  it("shows an in-progress aggregate status in the Plan & decompose header while an invocation is still running", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "pending", title: "Task A" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json(["inv_001"])),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "implementation_plan",
          status: "running",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: null,
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await waitFor(() => {
      expect(screen.getByTestId("task-section-status-ungrouped")).toHaveTextContent(
        "in progress",
      );
    });
  });

  it("shows a failed aggregate status in the Plan & decompose header when any invocation failed, even amid in-progress ones", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "pending", title: "Task A" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001", "inv_002"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "implementation_plan",
          status: "running",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: null,
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/invocations/inv_002", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_002",
          namespace: "alan.workspace.t1",
          team: "decomposer",
          status: "failed",
          started_at: "2026-05-12T08:01:00Z",
          completed_at: "2026-05-12T08:01:30Z",
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await waitFor(() => {
      expect(screen.getByTestId("task-section-status-ungrouped")).toHaveTextContent("failed");
    });
  });

  it("shows planning-state legend in task-graph section when an outline-level task exists", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "a", depends_on: [], team: "implementation_plan", status: "completed", planning_state: "outline" },
            { id: "b", depends_on: ["a"], team: null, status: "running" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const legend = await screen.findByTestId("planning-state-legend");
    expect(legend).toHaveTextContent("D = Detailed plan · O = Outline");
  });

  it("hides planning-state legend when no task is at the outline level", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "a", depends_on: [], team: "implementation_plan", status: "completed", planning_state: "detailed" },
            { id: "b", depends_on: ["a"], team: null, status: "running" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("inter-task-dag");
    expect(screen.queryByTestId("planning-state-legend")).toBeNull();
  });

  it("starts the Plan & decompose group and every per-task group open with ▾ and toggles each on click, Enter and Space", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "running", title: "Task A" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_plan"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_plan", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_plan",
          namespace: "alan.workspace.t1",
          team: "implementation_plan",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const groups = [
      await screen.findByTestId("task-section-ungrouped"),
      screen.getByTestId("task-section-task-a"),
      screen.getByTestId("task-section-task-b"),
    ];
    for (const group of groups) {
      await expectDisclosureState(group, true);
    }
    for (const group of groups) {
      await expectTogglesOnEveryActivation(group);
    }
  });

  it("keeps the group status, run count and cost beside the marker in the summary", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "running", title: "Task A" },
            { id: "task-b", depends_on: ["task-a"], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const group = await screen.findByTestId("task-section-task-a");
    const summary = disclosureSummary(group);
    expect(summary.firstElementChild).toHaveTextContent("▾");
    expect(within(summary).getByTestId("task-section-heading-task-a")).toHaveTextContent("Task A");
    expect(within(summary).getByTestId("task-section-status-task-a")).toHaveTextContent("in progress");
    expect(within(summary).getByTestId("task-section-run-count-task-a")).toHaveTextContent("0 runs");
  });

  it("shows cost roll-up in task section when invocation has cost data", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "task-a", depends_on: [], team: "implementation", status: "completed", title: "Task A" },
            { id: "task-b", depends_on: [], team: "verification", status: "pending", title: "Task B" },
          ],
        }),
      ),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.5",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "implementation",
          source_task_id: "task-a",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [
            {
              id: "n",
              type: "io.alan.LLMStep",
              role: "n",
              model: "opus",
              depends_on: [],
              status: "success",
              started_at: null,
              completed_at: null,
              stream_path: null,
              dispatched: [],
              error: null,
              input_tokens: 100,
              output_tokens: 50,
              cache_creation_input_tokens: null,
              cache_read_input_tokens: null,
              total_cost_usd: 0.0012,
              usage_raw: null,
            },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("task-section-cost-task-a")).toBeInTheDocument();
  });
});
