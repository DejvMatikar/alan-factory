import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type DispatchInstructionsMap, type Handover } from "../../../api";
import { server } from "../../../test/server";
import { makeHandover as makeSharedHandover } from "../../../test/workspaceFixtures";
import { makeTask } from "../../../tests/fixtures/workflowTask";
import type { DocsState } from "../hooks/useInvocationDocs";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import type { DocState } from "./InvocationCard";
import InvocationGraph from "./InvocationGraph";

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
  const fitView = vi.fn().mockResolvedValue(true);
  function ReactFlow({
    nodes,
    edges,
    nodeTypes,
    children,
  }: {
    nodes: { id: string; type: string; data: Record<string, unknown> }[];
    edges: { id: string; source: string; target: string }[];
    nodeTypes: Record<
      string,
      React.ComponentType<{ data: Record<string, unknown> }>
    >;
    children?: React.ReactNode;
  }) {
    return React.createElement(
      "div",
      { "data-testid": "rf-mock" },
      nodes.map((n) => {
        const Component = nodeTypes[n.type];
        if (!Component) return null;
        return React.createElement(Component, { key: n.id, data: n.data });
      }),
      edges.map((e) =>
        React.createElement("div", {
          key: e.id,
          "data-testid": `rf-edge-${e.id}`,
          "data-source": e.source,
          "data-target": e.target,
        }),
      ),
      children,
    );
  }
  function Stub() {
    return null;
  }
  function useReactFlow() {
    return { fitView };
  }
  return { ReactFlow, Background: Stub, Controls: Stub, useReactFlow };
});

function makeDocState(overrides: Partial<DocState> = {}): DocState {
  const { sourceTaskId = null, ...rest } = overrides;
  return {
    team: "research",
    status: "success",
    startedAt: "2026-05-12T08:00:00Z",
    completedAt: "2026-05-12T08:00:30Z",
    tasks: [],
    ...rest,
    sourceTaskId,
  };
}

function makeDocsState(
  entries: [string, DocState | null][],
  errEntries: [string, string][] = [],
): DocsState {
  const docs = new Map<string, DocState | null>(entries);
  const errors = new Map<string, string | null>();
  for (const [id] of entries) errors.set(id, null);
  for (const [id, err] of errEntries) errors.set(id, err);
  return { docs, errors, allSettled: true };
}

function makeHandover(overrides: Partial<Handover> = {}): Handover {
  return makeSharedHandover({
    handover_id: "handover_001",
    to_team_suggestions: ["architecture"],
    timestamp_utc: "2026-05-12T08:00:30Z",
    full_synthesis: "test summary",
    ...overrides,
  });
}

const LIVE_EMPTY: LiveWorkspaceState = { invocations: {}, streamError: null, notices: [], stickyNotice: null };
const DOCS_EMPTY: DocsState = { docs: new Map(), errors: new Map(), allSettled: true };

const READBACKS_EMPTY: DispatchInstructionsMap = {};

function renderGraph(
  invocations: string[],
  docsState: DocsState = DOCS_EMPTY,
  live: LiveWorkspaceState = LIVE_EMPTY,
  readbacks: DispatchInstructionsMap = READBACKS_EMPTY,
  repo: string | null = "DejvMatikar/alan",
) {
  return render(
    <InvocationGraph
      taskId="t1"
      invocations={invocations}
      docsState={docsState}
      live={live}
      readbacks={readbacks}
      repo={repo}
    />,
    { wrapper: MemoryRouter },
  );
}

async function clickStep(invId: string, stepId: string) {
  const card = await screen.findByTestId(`workflow-node-${invId}-${stepId}`);
  await userEvent.click(within(card).getByRole("button", { name: /^Show activity of / }));
}

describe("InvocationGraph", () => {
  beforeEach(() => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([]);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows empty state when invocations is empty", () => {
    renderGraph([]);
    expect(screen.getByTestId("invocation-graph-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("invocation-graph")).not.toBeInTheDocument();
  });

  it("renders graph container with one invocation", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    expect(screen.getByTestId("invocation-graph")).toBeInTheDocument();
    await screen.findByTestId("invocation-node-inv_001");
  });

  it("renders node in loading state when doc is absent from docsState", async () => {
    renderGraph(["inv_001"], { docs: new Map(), errors: new Map(), allSettled: false });
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(node.dataset.status).toBe("loading");
    expect(node).toHaveTextContent("Loading…");
  });

  it("renders team name and status badge when doc is populated", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ team: "architecture", status: "success" })]]));
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(node).toHaveTextContent("Architecture");
    expect(node.dataset.status).toBe("success");
    expect(screen.getByTestId("node-status-success")).toBeInTheDocument();
  });

  it("applies override label for pr_merge team", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ team: "pr_merge" })]]));
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(node).toHaveTextContent("PR Merge");
  });

  it("maps escalated status to failed badge", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ status: "escalated" })]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(screen.getByTestId("node-status-failed")).toBeInTheDocument();
  });

  it("shows running… duration for in-progress invocation", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ status: "running", completedAt: null })]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(screen.getByText("running…")).toBeInTheDocument();
  });

  it("hides duration for pending invocation", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ status: "pending", completedAt: null })]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(screen.queryByText("running…")).not.toBeInTheDocument();
  });

  it("shows singular step label for one task", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("a")] })]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(screen.getByText("1 step")).toBeInTheDocument();
  });

  it("shows plural steps label for multiple tasks", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("a"), makeTask("b")] })]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(screen.getByText("2 steps")).toBeInTheDocument();
  });

  it("applies live nodeUpdates to step count", async () => {
    renderGraph(
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("a")] })]]),
      {
        invocations: { inv_001: { nodeUpdates: [makeTask("b", { status: "running" })], liveStreams: {} } },
        streamError: null,
        notices: [],
        stickyNotice: null,
      },
    );
    await screen.findByTestId("invocation-node-inv_001");
    expect(screen.getByText("2 steps")).toBeInTheDocument();
  });

  it("preserves passed invocation order without sorting", async () => {
    renderGraph(
      ["inv_002", "inv_001"],
      makeDocsState([["inv_002", makeDocState()], ["inv_001", makeDocState()]]),
    );
    await screen.findByTestId("invocation-node-inv_001");
    await screen.findByTestId("invocation-node-inv_002");
    const graph = screen.getByTestId("invocation-graph");
    const nodes = graph.querySelectorAll("[data-testid^='invocation-node-']");
    expect(nodes[0]!.getAttribute("data-testid")).toBe("invocation-node-inv_002");
    expect(nodes[1]!.getAttribute("data-testid")).toBe("invocation-node-inv_001");
  });

  it("step nodes and edges are hidden by default (group collapsed)", async () => {
    renderGraph(
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("a"), makeTask("b", { depends_on: ["a"] })] })]]),
    );
    await screen.findByText("2 steps");
    expect(screen.queryByTestId("workflow-node-inv_001-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("workflow-node-inv_001-b")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rf-edge-step-inv_001-a->b")).not.toBeInTheDocument();
  });

  it("clicking a group expands it and shows step nodes", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("orchestrator")] })]]));
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    expect(await screen.findByTestId("workflow-node-inv_001-orchestrator")).toBeInTheDocument();
  });

  it("clicking a step opens the stream panel", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/orchestrator/stream",
        () => HttpResponse.json([]),
      ),
    );
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("orchestrator", { status: "running" })] })]]));
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "orchestrator");
    expect(await screen.findByTestId("stream-panel")).toBeInTheDocument();
  });

  it("stream-panel-close button hides the stream panel", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/orchestrator/stream",
        () => HttpResponse.json([]),
      ),
    );
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("orchestrator", { status: "running" })] })]]));
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "orchestrator");
    await screen.findByTestId("stream-panel");
    await userEvent.click(screen.getByTestId("stream-panel-close"));
    await waitFor(() => {
      expect(screen.queryByTestId("stream-panel")).not.toBeInTheDocument();
    });
  });

  it("shows error state when invocation has an error in docsState", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", null]], [["inv_001", "not found"]]));
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(node.dataset.status).toBe("error");
    expect(node).toHaveTextContent("Load failed");
  });

  it("no stream panel when live-update task disappears", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/a/stream",
        () => HttpResponse.json([]),
      ),
    );
    const docsState = makeDocsState([["inv_001", makeDocState()]]);
    const { rerender } = renderGraph(["inv_001"], docsState, {
      invocations: {
        inv_001: { nodeUpdates: [makeTask("a", { status: "running" })], liveStreams: {} },
      },
      streamError: null,
      notices: [],
      stickyNotice: null,
    });
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "a");
    await screen.findByTestId("stream-panel");

    rerender(
      <InvocationGraph
        taskId="t1"
        invocations={["inv_001"]}
        docsState={docsState}
        live={LIVE_EMPTY}
        readbacks={READBACKS_EMPTY}
        repo="DejvMatikar/alan"
      />,
    );
    await waitFor(() => {
      expect(screen.queryByTestId("stream-panel")).not.toBeInTheDocument();
    });
  });

  it("no stream panel when selection points to removed invocation (stale selection)", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/a/stream",
        () => HttpResponse.json([]),
      ),
      http.get(
        "/api/workspaces/t1/invocations/inv_002/agents/b/stream",
        () => HttpResponse.json([]),
      ),
    );
    const ds = makeDocsState([
      ["inv_001", makeDocState({ tasks: [makeTask("a")] })],
      ["inv_002", makeDocState({ tasks: [makeTask("b")] })],
    ]);
    const { rerender } = renderGraph(["inv_001", "inv_002"], ds);
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "a");
    await screen.findByTestId("stream-detail-inv_001");

    rerender(
      <InvocationGraph
        taskId="t1"
        invocations={["inv_002"]}
        docsState={makeDocsState([["inv_002", makeDocState({ tasks: [makeTask("b")] })]])}
        live={LIVE_EMPTY}
        readbacks={READBACKS_EMPTY}
        repo="DejvMatikar/alan"
      />,
    );
    await waitFor(() => {
      expect(screen.queryByTestId("stream-detail-inv_001")).not.toBeInTheDocument();
    });
  });

  it("failed task: stream panel shows node error", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/worker/stream",
        () => HttpResponse.json([]),
      ),
    );
    renderGraph(
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("worker", { status: "failed", error: "process exited" })] })]]),
    );
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "worker");
    expect(await screen.findByTestId("stream-node-error")).toBeInTheDocument();
  });

  it("selecting a step in a second invocation replaces the active stream panel", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/alpha/stream",
        () => HttpResponse.json([]),
      ),
      http.get(
        "/api/workspaces/t1/invocations/inv_002/agents/beta/stream",
        () => HttpResponse.json([]),
      ),
    );
    renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([
        ["inv_001", makeDocState({ status: "running", tasks: [makeTask("alpha", { status: "running" })] })],
        ["inv_002", makeDocState({ status: "running", tasks: [makeTask("beta", { status: "running" })] })],
      ]),
    );
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "alpha");
    expect(await screen.findByTestId("stream-detail-inv_001")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("invocation-node-inv_002"));
    await clickStep("inv_002", "beta");
    expect(await screen.findByTestId("stream-detail-inv_002")).toBeInTheDocument();
    expect(screen.queryByTestId("stream-detail-inv_001")).not.toBeInTheDocument();
  });

  it("clicking a group node toggles expand and collapse (nodes + edges)", async () => {
    renderGraph(
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("a"), makeTask("b", { depends_on: ["a"] })] })]]),
    );
    await screen.findByText("2 steps");
    const node = screen.getByTestId("invocation-node-inv_001");
    expect(node.className).not.toContain("ring-1");
    expect(node).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("workflow-node-inv_001-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rf-edge-step-inv_001-a->b")).not.toBeInTheDocument();

    await userEvent.click(node);
    await waitFor(() => {
      expect(screen.getByTestId("invocation-node-inv_001").className).toContain("ring-1");
    });
    expect(screen.getByTestId("invocation-node-inv_001")).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByTestId("workflow-node-inv_001-a")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-node-inv_001-b")).toBeInTheDocument();
    expect(screen.getByTestId("rf-edge-step-inv_001-a->b")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("invocation-node-inv_001"));
    await waitFor(() => {
      expect(screen.getByTestId("invocation-node-inv_001").className).not.toContain("ring-1");
    });
    expect(screen.getByTestId("invocation-node-inv_001")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("workflow-node-inv_001-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("workflow-node-inv_001-b")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rf-edge-step-inv_001-a->b")).not.toBeInTheDocument();
  });

  it("stream panel closes when its group is collapsed", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/orchestrator/stream",
        () => HttpResponse.json([]),
      ),
    );
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("orchestrator", { status: "running" })] })]]));
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "orchestrator");
    expect(await screen.findByTestId("stream-panel")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("invocation-node-inv_001"));
    await waitFor(() => {
      expect(screen.queryByTestId("stream-panel")).not.toBeInTheDocument();
    });
  });

  it("detail falls back to empty streams when invocation not in live state", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/orchestrator/stream",
        () => HttpResponse.json([]),
      ),
    );
    renderGraph(
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState({ status: "running", tasks: [makeTask("orchestrator", { status: "running" })] })]]),
    );
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "orchestrator");
    expect(await screen.findByTestId("stream-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("stream-row-assistant")).not.toBeInTheDocument();
  });

  it("passes live stream events to stream panel", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/orchestrator/stream",
        () => HttpResponse.json([]),
      ),
    );
    renderGraph(
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState({ status: "running", tasks: [makeTask("orchestrator", { status: "running" })] })]]),
      {
        invocations: {
          inv_001: {
            nodeUpdates: [],
            liveStreams: { orchestrator: [{ id: "e1", event: { type: "text" } }] },
          },
        },
        streamError: null,
        notices: [],
        stickyNotice: null,
      },
    );
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "orchestrator");
    expect(await screen.findByTestId("stream-panel")).toBeInTheDocument();
  });

  it("swallows fitView rejection without crashing", async () => {
    const mod = await import("@xyflow/react");
    const reactFlow = mod.useReactFlow() as unknown as {
      fitView: ReturnType<typeof vi.fn>;
    };
    reactFlow.fitView.mockRejectedValueOnce(new Error("fit failed"));
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv_001");
    await waitFor(() => {
      expect(reactFlow.fitView).toHaveBeenCalled();
    });
  });

  it("renders handover nodes between invocations", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([makeHandover()]);
    renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
    );
    await screen.findByTestId("invocation-node-inv_001");
    expect(await screen.findByTestId("handover-node-handover_001")).toBeInTheDocument();
  });

  it("clicking a handover node opens handover-panel", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([makeHandover()]);
    renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
    );
    await userEvent.click(await screen.findByTestId("handover-node-handover_001"));
    expect(await screen.findByTestId("handover-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("stream-panel")).not.toBeInTheDocument();
  });

  it("selecting a step still opens stream-panel after handover integration (regression)", async () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/orchestrator/stream",
        () => HttpResponse.json([]),
      ),
    );
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("orchestrator", { status: "running" })] })]]));
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await clickStep("inv_001", "orchestrator");
    expect(await screen.findByTestId("stream-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("handover-panel")).not.toBeInTheDocument();
  });

  it("threads the passed repo through to the handover panel's PR link", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([
      makeHandover({ pr_number: 42, artifacts_produced: [{ path: "/tmp/x.ts", description: "x" }] }),
    ]);
    renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
      LIVE_EMPTY,
      READBACKS_EMPTY,
      "acme-corp/sprocket",
    );
    await userEvent.click(await screen.findByTestId("handover-node-handover_001"));
    expect(await screen.findByTestId("pr-link")).toHaveAttribute(
      "href",
      "https://github.com/acme-corp/sprocket/pull/42",
    );
  });

  it("renders a degraded, non-clickable PR reference in the handover panel when repo is null", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([
      makeHandover({ pr_number: 42, artifacts_produced: [{ path: "/tmp/x.ts", description: "x" }] }),
    ]);
    renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
      LIVE_EMPTY,
      READBACKS_EMPTY,
      null,
    );
    await userEvent.click(await screen.findByTestId("handover-node-handover_001"));
    const el = await screen.findByTestId("pr-link");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveTextContent("repository unresolved");
  });

  it("close button hides handover panel", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([makeHandover()]);
    renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
    );
    await userEvent.click(await screen.findByTestId("handover-node-handover_001"));
    await screen.findByTestId("handover-panel");
    await userEvent.click(screen.getByTestId("handover-panel-close"));
    await waitFor(() => {
      expect(screen.queryByTestId("handover-panel")).not.toBeInTheDocument();
    });
  });

  it("getHandovers error: graph still renders, no panel crash", async () => {
    vi.spyOn(api, "getHandovers").mockRejectedValueOnce(new Error("fetch failed"));
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    expect(await screen.findByTestId("invocation-node-inv_001")).toBeInTheDocument();
    expect(screen.queryByTestId("handover-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("handover-node-handover_001")).not.toBeInTheDocument();
  });

  it("getHandovers error: shows handover-load-error indicator", async () => {
    vi.spyOn(api, "getHandovers").mockRejectedValueOnce(new Error("fetch failed"));
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    expect(await screen.findByTestId("handover-load-error")).toBeInTheDocument();
  });

  it("injects handover edges and removes direct chain edge", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([makeHandover()]);
    renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
    );
    await screen.findByTestId("invocation-node-inv_001");
    await screen.findByTestId("invocation-node-inv_002");
    await screen.findByTestId("handover-node-handover_001");
    expect(screen.queryByTestId("rf-edge-chain-inv_001->inv_002")).not.toBeInTheDocument();
    expect(screen.getByTestId("rf-edge-chain-inv_001->handover-handover_001")).toBeInTheDocument();
    expect(screen.getByTestId("rf-edge-chain-handover-handover_001->inv_002")).toBeInTheDocument();
  });

  it("skips handover node when from_invocation_id not in rendered invocations", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([
      makeHandover({ from_invocation_id: "inv_999" }),
    ]);
    renderGraph(["inv_002"], makeDocsState([["inv_002", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv_002");
    expect(screen.queryByTestId("handover-node-handover_001")).not.toBeInTheDocument();
  });

  it("handover nodes render correct color class for each non-default type", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([
      makeHandover({ handover_id: "h1", handover_type: "clarification_request" }),
      makeHandover({ handover_id: "h2", handover_type: "revisit_amend" }),
      makeHandover({ handover_id: "h3", handover_type: "parallel_fanout_join" }),
      makeHandover({ handover_id: "h4", handover_type: "escalation" }),
    ]);
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    expect((await screen.findByTestId("handover-node-h1")).className).toContain("border-yellow-700");
    expect(screen.getByTestId("handover-node-h2").className).toContain("border-orange-700");
    expect(screen.getByTestId("handover-node-h3").className).toContain("border-blue-700");
    expect(screen.getByTestId("handover-node-h4").className).toContain("border-red-700");
  });

  it("handover node shows 'await_human' sentinel when non-terminal and to_team_suggestions is empty", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([
      makeHandover({ handover_type: "forward", to_team_suggestions: [] }),
    ]);
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    expect(await screen.findByTestId("handover-node-handover_001")).toHaveTextContent("await_human");
  });

  it("handover node shows 'Completed' with green border for terminal handover type", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([
      makeHandover({ handover_type: "terminal", to_team_suggestions: [] }),
    ]);
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    const node = await screen.findByTestId("handover-node-handover_001");
    expect(node).toHaveTextContent("Completed");
    expect(node.className).toContain("border-green-500");
  });

  it("handover node shows 'Escalated' label for escalation handover type with empty suggestions", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([
      makeHandover({ handover_type: "escalation", to_team_suggestions: [] }),
    ]);
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    expect(await screen.findByTestId("handover-node-handover_001")).toHaveTextContent("Escalated");
  });

  it("handover node remains visible after anchor group is expanded", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([makeHandover()]);
    renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([
        ["inv_001", makeDocState({ tasks: [makeTask("worker")] })],
        ["inv_002", makeDocState()],
      ]),
    );
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    expect(await screen.findByTestId("handover-node-handover_001")).toBeInTheDocument();
  });

  it("no-op when component unmounts before handovers fetch resolves", async () => {
    let resolveHandovers!: (v: Handover[]) => void;
    vi.spyOn(api, "getHandovers").mockReturnValueOnce(
      new Promise<Handover[]>((r) => {
        resolveHandovers = r;
      }),
    );
    const { unmount } = renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv_001");
    unmount();
    resolveHandovers([]);
  });

  it("no-op when component unmounts before handovers error arrives", async () => {
    let rejectHandovers!: (v: unknown) => void;
    vi.spyOn(api, "getHandovers").mockReturnValueOnce(
      new Promise<Handover[]>((_, r) => {
        rejectHandovers = r;
      }),
    );
    const { unmount } = renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv_001");
    unmount();
    rejectHandovers(new Error("too late"));
  });

  it("useHandovers String(err) path: non-Error rejection does not crash", async () => {
    vi.spyOn(api, "getHandovers").mockRejectedValueOnce("plain string error");
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(screen.queryByTestId("handover-panel")).not.toBeInTheDocument();
  });

  it("clicking output icon on step opens NodeDetailModal", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001/agents/orchestrator/result", () =>
        HttpResponse.json({
          role: "orchestrator",
          model: "sonnet",
          result_text: "done",
          completed_at: "2026-05-12T08:00:10Z",
        }),
      ),
    );
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("orchestrator", { status: "success" })] })]]));
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await screen.findByTestId("workflow-node-inv_001-orchestrator");
    await userEvent.click(screen.getByTestId("icon-output"));
    expect(await screen.findByTestId("node-detail-dialog")).toBeInTheDocument();
  });

  it("node detail modal can be closed via close button in graph view", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001/agents/orchestrator/result", () =>
        HttpResponse.json({
          role: "orchestrator",
          model: "sonnet",
          result_text: "done",
          completed_at: "2026-05-12T08:00:10Z",
        }),
      ),
    );
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("orchestrator", { status: "success" })] })]]));
    await userEvent.click(await screen.findByTestId("invocation-node-inv_001"));
    await screen.findByTestId("workflow-node-inv_001-orchestrator");
    await userEvent.click(screen.getByTestId("icon-output"));
    await screen.findByTestId("node-detail-dialog");
    await userEvent.click(screen.getByTestId("node-detail-close"));
    await waitFor(() => {
      expect(screen.queryByTestId("node-detail-dialog")).not.toBeInTheDocument();
    });
  });

  it("handover panel hides when selected handover is no longer in list after re-fetch", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValueOnce([makeHandover()]);
    const { rerender } = renderGraph(
      ["inv_001", "inv_002"],
      makeDocsState([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
    );
    await userEvent.click(await screen.findByTestId("handover-node-handover_001"));
    await screen.findByTestId("handover-panel");

    rerender(
      <InvocationGraph
        taskId="t1"
        invocations={["inv_001", "inv_002", "inv_003"]}
        docsState={makeDocsState([
          ["inv_001", makeDocState()],
          ["inv_002", makeDocState()],
          ["inv_003", makeDocState()],
        ])}
        live={LIVE_EMPTY}
        readbacks={READBACKS_EMPTY}
        repo="DejvMatikar/alan"
      />,
    );
    await waitFor(() => {
      expect(screen.queryByTestId("handover-panel")).not.toBeInTheDocument();
    });
  });

  it("renders invocation-node usage line when tasks have token and cost data", async () => {
    const task = makeTask("orchestrator", {
      input_tokens: 100,
      output_tokens: 50,
      total_cost_usd: 0.001,
    });
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [task] })]]));
    const usageEl = await screen.findByTestId("invocation-node-inv_001-usage");
    expect(usageEl).toHaveTextContent("100↑ 50↓ · $0.0010");
  });

  it("omits usage line when all tasks have null token and cost data", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks: [makeTask("orchestrator")] })]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(screen.queryByTestId("invocation-node-inv_001-usage")).not.toBeInTheDocument();
  });

  it("sums usage across multiple tasks on the team node", async () => {
    const tasks = [
      makeTask("a", { input_tokens: 100, output_tokens: 50, total_cost_usd: 0.001 }),
      makeTask("b", { input_tokens: 200, output_tokens: 80, total_cost_usd: 0.002 }),
    ];
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState({ tasks })]]));
    const usageEl = await screen.findByTestId("invocation-node-inv_001-usage");
    expect(usageEl).toHaveTextContent("300↑ 130↓ · $0.0030");
  });

  it("renders an instruction badge on the group node when a readback is present", async () => {
    renderGraph(
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState()]]),
      LIVE_EMPTY,
      { inv_001: { status: "attached", text: "build the widget", origin: null } },
    );
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(within(node).getByTestId("instruction-badge-inv_001")).toHaveTextContent(
      "instructions attached",
    );
    expect(within(node).queryByTestId("instruction-badge-inv_001-details")).not.toBeInTheDocument();
  });

  it("omits the instruction badge on the group node when no readback is present (loading/error)", async () => {
    renderGraph(["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(within(node).queryByTestId("instruction-badge-inv_001")).not.toBeInTheDocument();
  });

  it("renders a muted 'no instructions' badge for status=none on the group node", async () => {
    renderGraph(
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState()]]),
      LIVE_EMPTY,
      { inv_001: { status: "none", text: null, origin: null } },
    );
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(within(node).getByTestId("instruction-badge-inv_001")).toHaveTextContent(
      "no instructions",
    );
  });
});
