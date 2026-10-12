import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../../api";
import { server } from "../../../test/server";
import { makeTask } from "../../../tests/fixtures/workflowTask";
import type { DocsState } from "../hooks/useInvocationDocs";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import type { DocState } from "./InvocationCard";
import InvocationGraph from "./InvocationGraph";

const graph = vi.hoisted(() => ({ nodes: [] as { id: string; type: string; focusable?: boolean }[] }));

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
    children,
  }: {
    nodes: { id: string; type: string; focusable?: boolean; data: Record<string, unknown> }[];
    nodeTypes: Record<string, React.ComponentType<{ data: Record<string, unknown> }>>;
    children?: React.ReactNode;
  }) {
    graph.nodes = nodes;
    return React.createElement(
      "div",
      { "data-testid": "rf-mock" },
      nodes.map((n) => {
        const Component = nodeTypes[n.type];
        if (!Component) return null;
        return React.createElement(Component, { key: n.id, data: n.data });
      }),
      children,
    );
  }
  function Stub() {
    return null;
  }
  return {
    ReactFlow,
    Background: Stub,
    Controls: Stub,
    useReactFlow: () => ({ fitView: vi.fn().mockResolvedValue(true) }),
  };
});

const LIVE_EMPTY: LiveWorkspaceState = { invocations: {}, streamError: null, notices: [], stickyNotice: null };

function makeDocState(overrides: Partial<DocState> = {}): DocState {
  return {
    team: "research",
    status: "success",
    startedAt: "2026-05-12T08:00:00Z",
    completedAt: "2026-05-12T08:00:30Z",
    sourceTaskId: null,
    tasks: [],
    ...overrides,
  };
}

function docsStateFor(entries: [string, DocState][]): DocsState {
  const docs = new Map<string, DocState | null>(entries);
  const errors = new Map<string, string | null>(entries.map(([id]) => [id, null]));
  return { docs, errors, allSettled: true };
}

function graphFor(invocations: string[], docsState: DocsState) {
  return (
    <InvocationGraph
      taskId="t1"
      invocations={invocations}
      docsState={docsState}
      live={LIVE_EMPTY}
      readbacks={{}}
      repo="DejvMatikar/alan"
    />
  );
}

function renderGraph(invocations: string[], docsState: DocsState) {
  return render(graphFor(invocations, docsState), { wrapper: MemoryRouter });
}

const REVIEWER = makeTask("step_2", { role: "reviewer" });

function stubStepEndpoints() {
  server.use(
    http.get("/api/workspaces/t1/invocations/inv_001/agents/reviewer/stream", () => HttpResponse.json([])),
    http.get("/api/workspaces/t1/invocations/inv_001/agents/step_2/result", () =>
      HttpResponse.json({
        role: "reviewer",
        model: "sonnet",
        result_text: "done",
        completed_at: "2026-05-12T08:00:10Z",
      }),
    ),
  );
}

async function expandRun(invId: string) {
  await userEvent.click(await screen.findByTestId(`invocation-node-${invId}`));
}

async function openOutput(stepId: string, label: string) {
  await screen.findByTestId(`workflow-node-inv_001-${stepId}`);
  await userEvent.click(screen.getByRole("button", { name: `View output of ${label}` }));
  return screen.findByTestId("node-detail-dialog");
}

describe("InvocationGraph step detail", () => {
  beforeEach(() => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([]);
    stubStepEndpoints();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("names the pop-up after the resolved step label", async () => {
    renderGraph(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER] })]]));
    await expandRun("inv_001");
    const dialog = await openOutput("step_2", "reviewer (step_2)");
    expect(dialog).toHaveAccessibleName("reviewer (step_2)");
    expect(within(dialog).getByRole("heading", { level: 2 })).toHaveTextContent("reviewer (step_2)");
  });

  it("uses the bare role as the label when it equals the step id", async () => {
    renderGraph(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [makeTask("step_2")] })]]));
    await expandRun("inv_001");
    const dialog = await openOutput("step_2", "step_2");
    expect(dialog).toHaveAccessibleName("step_2");
  });

  it("falls back to the node id when the task disappears while the pop-up is open", async () => {
    const { rerender } = renderGraph(
      ["inv_001"],
      docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER] })]]),
    );
    await expandRun("inv_001");
    await openOutput("step_2", "reviewer (step_2)");
    rerender(graphFor(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [] })]])));
    expect(screen.getByTestId("node-detail-dialog")).toHaveAccessibleName("step_2");
  });

  it("falls back to the node id when the whole run disappears while the pop-up is open", async () => {
    const { rerender } = renderGraph(
      ["inv_001", "inv_002"],
      docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER] })], ["inv_002", makeDocState()]]),
    );
    await expandRun("inv_001");
    await openOutput("step_2", "reviewer (step_2)");
    rerender(graphFor(["inv_002"], docsStateFor([["inv_002", makeDocState()]])));
    expect(screen.getByTestId("node-detail-dialog")).toHaveAccessibleName("step_2");
  });

  it("returns focus to the icon that opened the pop-up once it closes", async () => {
    renderGraph(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER] })]]));
    await expandRun("inv_001");
    await openOutput("step_2", "reviewer (step_2)");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByTestId("node-detail-dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View output of reviewer (step_2)" })).toHaveFocus();
  });

  it("closes the stream panel with a named icon-only control that describes itself on focus and meets the 44px target", async () => {
    renderGraph(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER] })]]));
    await expandRun("inv_001");
    await userEvent.click(await screen.findByRole("button", { name: "Show activity of reviewer (step_2)" }));
    const close = await screen.findByTestId("stream-panel-close");
    expect(close).toHaveAccessibleName("Close stream panel");
    expect(close).toHaveAccessibleDescription("Close this step's event stream");
    expect(close).toHaveAttribute("data-icon-only", "true");
    expect(close.className).toContain("min-h-11");
    expect(close.className).toContain("min-w-11");
    const description = document.getElementById(close.getAttribute("aria-describedby") ?? "");
    expect(description).toHaveClass("sr-only");
    act(() => {
      close.focus();
    });
    expect(description).not.toHaveClass("sr-only");

    await userEvent.keyboard("{Enter}");

    expect(screen.queryByTestId("stream-detail-inv_001")).not.toBeInTheDocument();
  });

  it("scrolls the selected step's detail into view with block nearest", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    renderGraph(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER] })]]));
    await expandRun("inv_001");
    const card = await screen.findByTestId("workflow-node-inv_001-step_2");
    scrollIntoView.mockClear();
    await userEvent.click(within(card).getByRole("button", { name: "Show activity of reviewer (step_2)" }));
    await screen.findByTestId("stream-detail-inv_001");
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("scrolls again when another step in the same run is selected", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001/agents/writer/stream", () => HttpResponse.json([])),
    );
    renderGraph(
      ["inv_001"],
      docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER, makeTask("writer")] })]]),
    );
    await expandRun("inv_001");
    await userEvent.click(await screen.findByRole("button", { name: "Show activity of reviewer (step_2)" }));
    await screen.findByTestId("stream-detail-inv_001");
    scrollIntoView.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Show activity of writer" }));
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("scrolls again when another step with the same role is selected", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const twin = makeTask("step_3", { role: "reviewer" });
    renderGraph(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER, twin] })]]));
    await expandRun("inv_001");
    await userEvent.click(await screen.findByRole("button", { name: "Show activity of reviewer (step_2)" }));
    await screen.findByTestId("stream-detail-inv_001");
    scrollIntoView.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Show activity of reviewer (step_3)" }));
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("does not scroll before any step is selected", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    scrollIntoView.mockClear();
    renderGraph(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER] })]]));
    await expandRun("inv_001");
    await screen.findByTestId("workflow-node-inv_001-step_2");
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("builds step nodes that are not focusable wrappers", async () => {
    renderGraph(["inv_001"], docsStateFor([["inv_001", makeDocState({ tasks: [REVIEWER, makeTask("writer")] })]]));
    await expandRun("inv_001");
    await screen.findByTestId("workflow-node-inv_001-step_2");
    const steps = graph.nodes.filter((n) => n.type === "step");
    expect(steps).toHaveLength(2);
    expect(steps.map((n) => n.focusable)).toEqual([false, false]);
  });
});
