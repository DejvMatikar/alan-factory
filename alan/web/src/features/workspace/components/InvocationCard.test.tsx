import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { WorkflowDoc } from "../../../api";
import { server } from "../../../test/server";
import { makeTask } from "../../../tests/fixtures/workflowTask";
import InvocationCard from "./InvocationCard";

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute("open", "");
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute("open");
    };
  }
});

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
  }: {
    nodes: {
      id: string;
      type: string;
      data: Record<string, unknown>;
    }[];
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

const BASE_DOC: WorkflowDoc = {
  schema_version: "1.0",
  id: "inv_001",
  namespace: "alan.workspace.t1",
  team: "research",
  status: "success",
  started_at: "2026-05-12T08:00:00Z",
  completed_at: "2026-05-12T08:00:30Z",
  tasks: [makeTask("orchestrator", { model: "opus" })],
};

function happyInvocation(doc: WorkflowDoc = BASE_DOC) {
  return http.get(`/api/workspaces/t1/invocations/${doc.id}`, () =>
    HttpResponse.json(doc),
  );
}

describe("InvocationCard", () => {
  it("renders the card collapsed when defaultExpanded=false", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
        defaultExpanded={false}
      />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.queryByTestId("rf-mock")).toBeNull();
  });

  it("toggles expand/collapse on header button click", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
        defaultExpanded={false}
      />,
    );
    const toggle = await screen.findByTestId("invocation-toggle-inv_001");
    await userEvent.click(toggle);
    await screen.findByTestId("rf-mock");
    await userEvent.click(toggle);
    await waitFor(() => {
      expect(screen.queryByTestId("rf-mock")).toBeNull();
    });
  });

  it("gives the header toggle a name, an open state, a marker and a purpose description that follow its state", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
        defaultExpanded={false}
      />,
    );
    const toggle = await screen.findByTestId("invocation-toggle-inv_001");
    expect(toggle).toHaveAccessibleName("Expand invocation inv_001");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("▸");
    expect(toggle).toHaveAccessibleDescription("Show this run's steps");

    await userEvent.click(toggle);

    expect(toggle).toHaveAccessibleName("Collapse invocation inv_001");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveTextContent("▾");
    expect(toggle).toHaveAccessibleDescription("Hide this run's steps");
  });

  it("makes the header toggle a 44px icon-only target that reveals its description on focus", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    const toggle = await screen.findByTestId("invocation-toggle-inv_001");
    expect(toggle).toHaveAttribute("data-icon-only", "true");
    expect(toggle.className).toContain("min-h-11");
    expect(toggle.className).toContain("min-w-11");
    const description = document.getElementById(toggle.getAttribute("aria-describedby") ?? "");
    expect(description).toHaveClass("sr-only");

    await userEvent.tab();

    expect(toggle).toHaveFocus();
    expect(description).not.toHaveClass("sr-only");
  });

  it("toggles the card body with Enter and Space on the focused header toggle", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    const toggle = await screen.findByTestId("invocation-toggle-inv_001");
    await screen.findByTestId("rf-mock");
    toggle.focus();

    await userEvent.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.queryByTestId("rf-mock")).toBeNull();
    });

    await userEvent.keyboard(" ");
    await screen.findByTestId("rf-mock");
  });

  it("renders 'failed' status badge for escalated workflows", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        status: "escalated",
      }),
    );
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
      />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.getByTestId("node-status-failed")).toBeInTheDocument();
  });

  it("renders 'failed' status badge for interrupted (user-stopped) workflows", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        status: "interrupted",
      }),
    );
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
      />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.getByTestId("node-status-failed")).toBeInTheDocument();
  });

  it("falls back to data-status='loading' before the workflow loads", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001",
        () => new Promise<Response>(() => undefined),
      ),
    );
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
      />,
    );
    const card = await screen.findByTestId("invocation-card-inv_001");
    expect(card.dataset.status).toBe("loading");
  });

  it("renders no duration when start_at is unparseable", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        started_at: "not-a-date",
        completed_at: null,
      }),
    );
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
      />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.queryByText("running…")).not.toBeInTheDocument();
  });

  it("hides duration when invocation status is pending", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        status: "pending",
        completed_at: null,
        tasks: [makeTask("orchestrator", { status: "pending", started_at: null, completed_at: null })],
      }),
    );
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
      />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.queryByText("running…")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("node-status-pending").length).toBeGreaterThanOrEqual(1);
  });

  it("renders inv-level 'running…' when invocation is still running", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        status: "running",
        completed_at: null,
      }),
    );
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
      />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    const matches = await screen.findAllByText("running…");
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("renders inline node error + StreamPanel footer when a node failed", async () => {
    const failedTask = makeTask("orchestrator", {
      status: "failed",
      error: "claude CLI timed out after 120s",
    });
    server.use(
      happyInvocation({
        ...BASE_DOC,
        status: "failed",
        tasks: [failedTask],
      }),
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/orchestrator/stream",
        () => HttpResponse.json([]),
      ),
    );
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
      />,
    );
    const node = await screen.findByTestId("workflow-node-orchestrator");
    expect(node).toHaveTextContent("claude CLI timed out after 120s");
    expect(node).not.toHaveAttribute("title");
    await userEvent.click(within(node).getByRole("button", { name: "Show activity of orchestrator" }));
    const footer = await screen.findByTestId("stream-node-error");
    expect(footer).toHaveTextContent("claude CLI timed out after 120s");
  });

  it("applies nodeUpdates passed from parent on top of fetched tasks", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        tasks: [makeTask("orchestrator", { status: "running" })],
      }),
    );
    const update = makeTask("orchestrator", { status: "success" });
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[update]}
      />,
    );
    const node = await screen.findByTestId("workflow-node-orchestrator");
    expect(node.dataset.status).toBe("success");
  });

  it("clicking output icon opens NodeDetailModal", async () => {
    server.use(
      happyInvocation(),
      http.get("/api/workspaces/t1/invocations/inv_001/agents/orchestrator/result", () =>
        HttpResponse.json({
          role: "orchestrator",
          model: "opus",
          result_text: "done",
          completed_at: "2026-05-12T08:00:10Z",
        }),
      ),
    );
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    await screen.findByTestId("workflow-node-orchestrator");
    await userEvent.click(screen.getByTestId("icon-output"));
    expect(await screen.findByTestId("node-detail-dialog")).toBeInTheDocument();
  });

  it("names the node detail pop-up after the step, adding the id when it differs from the role", async () => {
    server.use(
      happyInvocation({ ...BASE_DOC, tasks: [makeTask("step_2", { role: "reviewer" })] }),
      http.get("/api/workspaces/t1/invocations/inv_001/agents/step_2/result", () =>
        HttpResponse.json({
          role: "reviewer",
          model: "opus",
          result_text: "done",
          completed_at: "2026-05-12T08:00:10Z",
        }),
      ),
    );
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    await screen.findByTestId("workflow-node-step_2");
    await userEvent.click(screen.getByRole("button", { name: "View output of reviewer (step_2)" }));
    expect(await screen.findByTestId("node-detail-dialog")).toHaveAccessibleName("reviewer (step_2)");
  });

  it("scrolls the stream panel into view with block nearest when a step is selected", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    server.use(
      happyInvocation(),
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/orchestrator/stream",
        () => HttpResponse.json([]),
      ),
    );
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    await screen.findByTestId("workflow-node-orchestrator");
    scrollIntoView.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Show activity of orchestrator" }));
    await screen.findByTestId("stream-panel");
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("does not scroll when no step is selected", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    server.use(happyInvocation());
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    await screen.findByTestId("workflow-node-orchestrator");
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("renders usage line in card header when tasks have token and cost data", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        tasks: [makeTask("orchestrator", { input_tokens: 100, output_tokens: 50, total_cost_usd: 0.0015 })],
      }),
    );
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    const usage = await screen.findByTestId("invocation-card-inv_001-usage");
    expect(usage).toHaveTextContent("100↑");
    expect(usage).toHaveTextContent("50↓");
    expect(usage).toHaveTextContent("$0.0015");
  });

  it("hides usage line when all tasks have null token and cost data", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.queryByTestId("invocation-card-inv_001-usage")).not.toBeInTheDocument();
  });

  it("node detail modal can be closed via close button", async () => {
    server.use(
      happyInvocation(),
      http.get("/api/workspaces/t1/invocations/inv_001/agents/orchestrator/result", () =>
        HttpResponse.json({
          role: "orchestrator",
          model: "opus",
          result_text: "done",
          completed_at: "2026-05-12T08:00:10Z",
        }),
      ),
    );
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    await screen.findByTestId("workflow-node-orchestrator");
    await userEvent.click(screen.getByTestId("icon-output"));
    await screen.findByTestId("node-detail-dialog");
    await userEvent.click(screen.getByTestId("node-detail-close"));
    await waitFor(() => {
      expect(screen.queryByTestId("node-detail-dialog")).not.toBeInTheDocument();
    });
  });

  it("omits the instruction badge when instructionReadback is not passed", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard taskId="t1" invId="inv_001" liveStreams={{}} nodeUpdates={[]} />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.queryByTestId("instruction-badge-inv_001")).not.toBeInTheDocument();
  });

  it("renders the instruction badge in the header when instructionReadback is attached", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
        instructionReadback={{ status: "attached", text: "build the widget", origin: null }}
      />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.getByTestId("instruction-badge-inv_001")).toHaveTextContent(
      "instructions attached",
    );
  });

  it("expands the instruction disclosure to reveal the full recorded text", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
        instructionReadback={{ status: "attached", text: "build the widget", origin: null }}
      />,
    );
    await userEvent.click(await screen.findByTestId("instruction-badge-inv_001"));
    expect(screen.getByTestId("instruction-badge-inv_001-text")).toHaveTextContent(
      "build the widget",
    );
  });

  it("renders a muted 'no instructions' badge when instructionReadback status is none", async () => {
    server.use(happyInvocation());
    render(
      <InvocationCard
        taskId="t1"
        invId="inv_001"
        liveStreams={{}}
        nodeUpdates={[]}
        instructionReadback={{ status: "none", text: null, origin: null }}
      />,
    );
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.getByTestId("instruction-badge-inv_001")).toHaveTextContent(
      "no instructions",
    );
  });
});
