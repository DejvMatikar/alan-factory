import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../../api";
import { makeTask } from "../../../tests/fixtures/workflowTask";
import type { DocsState } from "../hooks/useInvocationDocs";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import type { DocState } from "./InvocationCard";
import InvocationGraph from "./InvocationGraph";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
    children,
  }: {
    nodes: { id: string; type: string; data: Record<string, unknown> }[];
    nodeTypes: Record<string, React.ComponentType<{ data: Record<string, unknown> }>>;
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
    tasks: [makeTask("worker")],
    ...overrides,
  };
}

function docsStateFor(entries: [string, DocState | null][]): DocsState {
  const docs = new Map<string, DocState | null>(entries);
  const errors = new Map<string, string | null>(entries.map(([id]) => [id, null]));
  return { docs, errors, allSettled: true };
}

function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location">{location.pathname}</p>;
}

function renderGraph(taskId: string, invocations: string[], docsState: DocsState) {
  return render(
    <MemoryRouter>
      <Routes>
        <Route
          path="*"
          element={
            <>
              <InvocationGraph
                taskId={taskId}
                invocations={invocations}
                docsState={docsState}
                live={LIVE_EMPTY}
                readbacks={{}}
                repo="DejvMatikar/alan"
              />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

function linkFor(invId: string) {
  return screen.getByRole("link", { name: `Open full view of run ${invId}` });
}

describe("InvocationGraph full-view link", () => {
  beforeEach(() => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([]);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a labelled link on every run node", async () => {
    renderGraph(
      "t1",
      ["inv_001", "inv_002"],
      docsStateFor([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
    );
    await screen.findByTestId("invocation-node-inv_001");
    expect(linkFor("inv_001")).toHaveTextContent("Open full view");
    expect(linkFor("inv_002")).toHaveTextContent("Open full view");
  });

  it("points each link at that run's full-view page", async () => {
    renderGraph(
      "t1",
      ["inv_001", "inv_002"],
      docsStateFor([["inv_001", makeDocState()], ["inv_002", makeDocState()]]),
    );
    await screen.findByTestId("invocation-node-inv_001");
    expect(linkFor("inv_001")).toHaveAttribute("href", "/workspaces/t1/invocations/inv_001");
    expect(linkFor("inv_002")).toHaveAttribute("href", "/workspaces/t1/invocations/inv_002");
  });

  it("encodes the task and run ids in the href", async () => {
    renderGraph("a/b c", ["inv 1"], docsStateFor([["inv 1", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv 1");
    expect(linkFor("inv 1")).toHaveAttribute("href", "/workspaces/a%2Fb%20c/invocations/inv%201");
  });

  it("shows the link on a run that is still loading", async () => {
    renderGraph("t1", ["inv_001"], { docs: new Map(), errors: new Map(), allSettled: false });
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(node.dataset.status).toBe("loading");
    expect(linkFor("inv_001")).toBeVisible();
  });

  it("shows the link on a run that failed to load", async () => {
    renderGraph(
      "t1",
      ["inv_001"],
      { docs: new Map([["inv_001", null]]), errors: new Map([["inv_001", "gone"]]), allSettled: true },
    );
    const node = await screen.findByTestId("invocation-node-inv_001");
    expect(node.dataset.status).toBe("error");
    expect(linkFor("inv_001")).toBeVisible();
  });

  it("is a sibling of the expand toggle, never inside it", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    const toggle = await screen.findByTestId("invocation-node-inv_001");
    expect(toggle).not.toContainElement(linkFor("inv_001"));
    expect(within(toggle).queryByRole("link")).not.toBeInTheDocument();
    expect(toggle.parentElement).toContainElement(linkFor("inv_001"));
  });

  it("sits beside the toggle in normal layout flow so it cannot cover the run's text", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    const toggle = await screen.findByTestId("invocation-node-inv_001");
    expect(toggle.parentElement).toHaveClass("flex");
    expect(toggle).toHaveClass("min-w-0", "flex-1");
    expect(linkFor("inv_001")).toHaveClass("shrink-0");
    expect(linkFor("inv_001").className).not.toContain("absolute");
  });

  it("keeps a press on the link from starting a graph drag or pan", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(linkFor("inv_001")).toHaveClass("nodrag", "nopan");
  });

  it("navigates without expanding or collapsing the run", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    const toggle = await screen.findByTestId("invocation-node-inv_001");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(linkFor("inv_001"));
    expect(screen.getByTestId("location")).toHaveTextContent("/workspaces/t1/invocations/inv_001");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("leaves an expanded run expanded when the link is clicked", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    const toggle = await screen.findByTestId("invocation-node-inv_001");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(linkFor("inv_001"));
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("is reached by keyboard right after the run's toggle", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    const toggle = await screen.findByTestId("invocation-node-inv_001");
    await userEvent.tab();
    expect(toggle).toHaveFocus();
    await userEvent.tab();
    expect(linkFor("inv_001")).toHaveFocus();
  });

  it("follows the link on Enter from the keyboard", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv_001");
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByTestId("location")).toHaveTextContent("/workspaces/t1/invocations/inv_001");
  });

  it("keeps the toggle expanding and collapsing on its own", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    const toggle = await screen.findByTestId("invocation-node-inv_001");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByTestId("workflow-node-inv_001-worker")).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("location")).toHaveTextContent("/");
  });

  it("renders the link with the compact graph-card density", async () => {
    renderGraph("t1", ["inv_001"], docsStateFor([["inv_001", makeDocState()]]));
    await screen.findByTestId("invocation-node-inv_001");
    expect(linkFor("inv_001").className).toContain("min-h-6");
    expect(linkFor("inv_001").className).not.toContain("min-h-11");
  });
});
