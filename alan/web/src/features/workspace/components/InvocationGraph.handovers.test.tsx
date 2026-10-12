import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type DispatchInstructionsMap, type Handover } from "../../../api";
import { makeHandover as makeSharedHandover } from "../../../test/workspaceFixtures";
import type { DocsState } from "../hooks/useInvocationDocs";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import InvocationGraph from "./InvocationGraph";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  const fitView = vi.fn().mockResolvedValue(true);
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
  function useReactFlow() {
    return { fitView };
  }
  return { ReactFlow, Background: Stub, Controls: Stub, useReactFlow };
});

const LIVE_EMPTY: LiveWorkspaceState = { invocations: {}, streamError: null, notices: [], stickyNotice: null };
const DOCS_EMPTY: DocsState = { docs: new Map(), errors: new Map(), allSettled: true };
const READBACKS_EMPTY: DispatchInstructionsMap = {};

function makeHandover(overrides: Partial<Handover> = {}): Handover {
  return makeSharedHandover({
    handover_id: "handover_001",
    to_team_suggestions: ["architecture"],
    timestamp_utc: "2026-05-12T08:00:30Z",
    full_synthesis: "test summary",
    ...overrides,
  });
}

function graphFor(taskId: string, invocations: string[]) {
  return (
    <InvocationGraph
      taskId={taskId}
      invocations={invocations}
      docsState={DOCS_EMPTY}
      live={LIVE_EMPTY}
      readbacks={READBACKS_EMPTY}
      repo="DejvMatikar/alan"
    />
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("InvocationGraph handovers read", () => {
  it("reads the handovers of the task once per invocation list", async () => {
    const getHandovers = vi.spyOn(api, "getHandovers").mockResolvedValue([makeHandover()]);
    render(graphFor("t1", ["inv_001", "inv_002"]), { wrapper: MemoryRouter });
    expect(await screen.findByTestId("handover-node-handover_001")).toBeInTheDocument();
    expect(getHandovers).toHaveBeenCalledExactlyOnceWith("t1");
  });

  it("re-reads the handovers when the invocation list changes and drops the old ones meanwhile", async () => {
    let releaseSecond: () => void = () => undefined;
    const getHandovers = vi
      .spyOn(api, "getHandovers")
      .mockResolvedValueOnce([makeHandover()])
      .mockImplementationOnce(
        () =>
          new Promise<Handover[]>((resolve) => {
            releaseSecond = () => resolve([makeHandover({ handover_id: "handover_002" })]);
          }),
      );
    const { rerender } = render(graphFor("t1", ["inv_001", "inv_002"]), { wrapper: MemoryRouter });
    await screen.findByTestId("handover-node-handover_001");

    rerender(graphFor("t1", ["inv_001", "inv_002", "inv_003"]));
    await waitFor(() => {
      expect(screen.queryByTestId("handover-node-handover_001")).not.toBeInTheDocument();
    });
    expect(getHandovers).toHaveBeenCalledTimes(2);

    releaseSecond();
    expect(await screen.findByTestId("handover-node-handover_002")).toBeInTheDocument();
  });

  it("clears the load-failed indicator once a re-read succeeds", async () => {
    vi.spyOn(api, "getHandovers")
      .mockRejectedValueOnce(new Error("fetch failed"))
      .mockResolvedValueOnce([makeHandover()]);
    const { rerender } = render(graphFor("t1", ["inv_001", "inv_002"]), { wrapper: MemoryRouter });
    expect(await screen.findByTestId("handover-load-error")).toBeInTheDocument();

    rerender(graphFor("t1", ["inv_001", "inv_002", "inv_003"]));
    await screen.findByTestId("handover-node-handover_001");
    expect(screen.queryByTestId("handover-load-error")).not.toBeInTheDocument();
  });

  it("closes the handover panel with a named icon-only control that describes itself on focus and meets the 44px target", async () => {
    vi.spyOn(api, "getHandovers").mockResolvedValue([makeHandover()]);
    render(graphFor("t1", ["inv_001", "inv_002"]), { wrapper: MemoryRouter });
    await userEvent.click(await screen.findByTestId("handover-node-handover_001"));
    const close = await screen.findByTestId("handover-panel-close");
    expect(close).toHaveAccessibleName("Close panel");
    expect(close).toHaveAccessibleDescription("Close the handover details");
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

    expect(screen.queryByTestId("handover-panel")).not.toBeInTheDocument();
  });

  it("keeps the handovers of two tasks apart", async () => {
    vi.spyOn(api, "getHandovers").mockImplementation((taskId) =>
      Promise.resolve([makeHandover({ handover_id: `handover_${taskId}` })]),
    );
    const { rerender } = render(graphFor("t1", ["inv_001", "inv_002"]), { wrapper: MemoryRouter });
    await screen.findByTestId("handover-node-handover_t1");

    rerender(graphFor("t2", ["inv_001", "inv_002"]));
    await screen.findByTestId("handover-node-handover_t2");
    expect(screen.queryByTestId("handover-node-handover_t1")).not.toBeInTheDocument();
  });
});
