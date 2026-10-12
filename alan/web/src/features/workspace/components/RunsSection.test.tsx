import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { DispatchInstructionsMap } from "../../../api";
import { server } from "../../../test/server";
import { makeHandover } from "../../../test/workspaceFixtures";
import type { DocsState } from "../hooks/useInvocationDocs";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import type { DocState } from "./InvocationCard";
import RunsSection from "./RunsSection";

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
  return { ReactFlow, Background: Stub, Controls: Stub, useReactFlow: () => ({ fitView: vi.fn().mockResolvedValue(true) }) };
});

const LIVE_EMPTY: LiveWorkspaceState = { invocations: {}, streamError: null, notices: [], stickyNotice: null };
const READBACKS_EMPTY: DispatchInstructionsMap = {};

function makeDocState(entries: [string, DocState | null][]): DocsState {
  const docs = new Map<string, DocState | null>(entries);
  const errors = new Map<string, string | null>();
  for (const [id] of entries) errors.set(id, null);
  return { docs, errors, allSettled: true };
}

function makeDoc(overrides: Partial<DocState> = {}): DocState {
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

function renderSection(
  invocations: readonly string[],
  docsState: DocsState = { docs: new Map(), errors: new Map(), allSettled: true },
  live: LiveWorkspaceState = LIVE_EMPTY,
  readbacks: DispatchInstructionsMap = READBACKS_EMPTY,
) {
  return render(
    <RunsSection
      taskId="t1"
      invocations={invocations}
      live={live}
      docsState={docsState}
      readbacks={readbacks}
      repo="DejvMatikar/alan"
    />,
    { wrapper: MemoryRouter },
  );
}

describe("RunsSection", () => {
  it("renders the invocation-graph empty state directly when there are no invocations", async () => {
    server.use(http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])));
    renderSection([]);
    expect(await screen.findByTestId("invocation-graph-empty")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("defaults to the list view with one row per invocation, in order", async () => {
    server.use(http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])));
    renderSection(
      ["inv_001", "inv_002"],
      makeDocState([
        ["inv_001", makeDoc()],
        ["inv_002", makeDoc()],
      ]),
    );
    const list = await screen.findByTestId("runs-list");
    const rows = within(list).getAllByTestId(/^run-row-inv_/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "run-row-inv_001",
      "run-row-inv_002",
    ]);
  });

  it("does not mount the graph canvas before Graph is selected", async () => {
    server.use(http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])));
    renderSection(["inv_001"], makeDocState([["inv_001", makeDoc()]]));
    await screen.findByTestId("runs-list");
    expect(screen.queryByTestId("invocation-graph")).not.toBeInTheDocument();
  });

  it("mounts the graph only after selecting the Graph tab, and returns to the list when List is reselected", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])));
    renderSection(["inv_001"], makeDocState([["inv_001", makeDoc()]]));
    await screen.findByTestId("runs-list");

    await user.click(screen.getByRole("tab", { name: "Graph" }));
    expect(await screen.findByTestId("invocation-graph")).toBeInTheDocument();
    expect(screen.queryByTestId("runs-list")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "List" }));
    expect(await screen.findByTestId("runs-list")).toBeInTheDocument();
    expect(screen.queryByTestId("invocation-graph")).not.toBeInTheDocument();
  });

  it("switches tabs with ArrowRight and ArrowLeft", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])));
    renderSection(["inv_001"], makeDocState([["inv_001", makeDoc()]]));
    const listTab = await screen.findByRole("tab", { name: "List" });
    listTab.focus();

    await user.keyboard("{ArrowRight}");
    expect(await screen.findByTestId("invocation-graph")).toBeInTheDocument();

    await user.keyboard("{ArrowLeft}");
    expect(await screen.findByTestId("runs-list")).toBeInTheDocument();
  });

  it("shows a handover load error in the list view when the handovers fetch fails", async () => {
    server.use(
      http.get("/api/workspaces/t1/handovers", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    renderSection(["inv_001"], makeDocState([["inv_001", makeDoc()]]));
    expect(await screen.findByTestId("handover-load-error")).toBeInTheDocument();
  });

  it("does not duplicate the handover load error once the Graph tab is selected", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1/handovers", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    renderSection(["inv_001"], makeDocState([["inv_001", makeDoc()]]));
    expect(await screen.findByTestId("handover-load-error")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Graph" }));
    expect(await screen.findAllByTestId("handover-load-error")).toHaveLength(1);
  });

  it("resolves each row's hand-off target from the task's handovers", async () => {
    const handover = makeHandover({
      from_invocation_id: "inv_001",
      handover_type: "forward",
      to_team_suggestions: ["verification"],
    });
    server.use(http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([handover])));
    renderSection(["inv_001"], makeDocState([["inv_001", makeDoc()]]));
    expect(await screen.findByTestId("run-row-handoff-inv_001")).toHaveTextContent("verification");
  });

  it("forwards readbacks to each row's instruction badge", async () => {
    server.use(http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])));
    renderSection(
      ["inv_001"],
      makeDocState([["inv_001", makeDoc()]]),
      LIVE_EMPTY,
      { inv_001: { status: "attached", text: "do it", origin: null } },
    );
    expect(await screen.findByTestId("instruction-badge-inv_001")).toHaveTextContent(
      "instructions attached",
    );
  });

  it("keeps tab and tabpanel ids unique across two instances mounted at once, with the same taskId", async () => {
    server.use(http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([])));
    render(
      <MemoryRouter>
        <RunsSection
          taskId="t1"
          invocations={["inv_001"]}
          live={LIVE_EMPTY}
          docsState={makeDocState([["inv_001", makeDoc()]])}
          readbacks={READBACKS_EMPTY}
          repo={null}
        />
        <RunsSection
          taskId="t1"
          invocations={["inv_002"]}
          live={LIVE_EMPTY}
          docsState={makeDocState([["inv_002", makeDoc()]])}
          readbacks={READBACKS_EMPTY}
          repo={null}
        />
      </MemoryRouter>,
    );

    const listTabs = await screen.findAllByRole("tab", { name: "List" });
    expect(listTabs).toHaveLength(2);
    expect(listTabs[0]!.id).not.toBe(listTabs[1]!.id);

    const panels = screen.getAllByRole("tabpanel");
    expect(panels).toHaveLength(2);
    expect(panels[0]!.id).not.toBe(panels[1]!.id);
    expect(panels[0]!.getAttribute("aria-labelledby")).toBe(listTabs[0]!.id);
    expect(panels[1]!.getAttribute("aria-labelledby")).toBe(listTabs[1]!.id);
  });
});
