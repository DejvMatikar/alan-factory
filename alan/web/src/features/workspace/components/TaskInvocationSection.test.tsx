import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type DispatchInstructionsMap, type Task } from "../../../api";
import {
  activateDisclosure,
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { makeWorkspaceTask } from "../../../test/workspaceFixtures";
import { makeTask } from "../../../tests/fixtures/workflowTask";
import type { DocsState } from "../hooks/useInvocationDocs";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import type { DocState } from "./InvocationCard";
import TaskInvocationSection from "./TaskInvocationSection";

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
  return { ReactFlow, Background: Stub, Controls: Stub, useReactFlow: () => ({ fitView: vi.fn().mockResolvedValue(true) }) };
});

const LIVE_EMPTY: LiveWorkspaceState = { invocations: {}, streamError: null, notices: [], stickyNotice: null };

beforeEach(() => {
  vi.spyOn(api, "getHandovers").mockResolvedValue([]);
});
afterEach(() => {
  vi.restoreAllMocks();
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

function makeDocsState(entries: [string, DocState | null][]): DocsState {
  const docs = new Map<string, DocState | null>(entries);
  const errors = new Map<string, string | null>();
  for (const [id] of entries) errors.set(id, null);
  return { docs, errors, allSettled: true };
}

function makeTask_(id: string, overrides: Partial<Task> = {}): Task {
  return makeWorkspaceTask({ id, team: "research", status: "running", title: null, ...overrides });
}

function renderSection(
  task: Task,
  invocationIds: readonly string[],
  docsState: DocsState = { docs: new Map(), errors: new Map(), allSettled: true },
  live: LiveWorkspaceState = LIVE_EMPTY,
  wsTitle = "",
  readbacks: DispatchInstructionsMap = {},
  repo: string | null = "DejvMatikar/alan",
) {
  return render(
    <TaskInvocationSection
      taskId="ws1"
      task={task}
      wsTitle={wsTitle}
      invocationIds={invocationIds}
      live={live}
      docsState={docsState}
      readbacks={readbacks}
      repo={repo}
    />,
    { wrapper: MemoryRouter },
  );
}

describe("TaskInvocationSection", () => {
  it("renders section with task.title as heading when title is set", async () => {
    renderSection(makeTask_("t1", { title: "My Feature" }), []);
    expect(await screen.findByTestId("task-section-heading-t1")).toHaveTextContent("My Feature");
  });

  it("falls back to formatted team label when title is null", async () => {
    renderSection(makeTask_("t1", { title: null, team: "implementation" }), []);
    expect(await screen.findByTestId("task-section-heading-t1")).toHaveTextContent("Implementation");
  });

  it("falls back to formatted task.id when both title and team are null", async () => {
    renderSection(makeTask_("task-abc", { title: null, team: null }), []);
    expect(await screen.findByTestId("task-section-heading-task-abc")).toHaveTextContent("Task Abc");
  });

  it("falls back to wsTitle for root task when title is null and wsTitle is provided", async () => {
    renderSection(makeTask_("t1", { title: null }), [], undefined, undefined, "Workspace Title");
    expect(await screen.findByTestId("task-section-heading-t1")).toHaveTextContent("Workspace Title");
  });

  it("falls back to task.id for child task with parent_task_id when title is null", async () => {
    renderSection(makeTask_("child-t", { title: null, parent_task_id: "root-t" }), []);
    expect(await screen.findByTestId("task-section-heading-child-t")).toHaveTextContent("child-t");
  });

  it("shows correct status label for each task status", async () => {
    const cases: [Task["status"], string][] = [
      ["completed", "done"],
      ["awaiting_input", "needs you"],
      ["failed", "failed"],
      ["running", "in progress"],
      ["pending", "in progress"],
      ["ready", "in progress"],
    ];
    for (const [status, label] of cases) {
      const { unmount } = renderSection(makeTask_("t1", { status }), []);
      expect(await screen.findByTestId("task-section-status-t1")).toHaveTextContent(label);
      unmount();
    }
  });

  it("shows singular 'run' for one invocation", async () => {
    renderSection(makeTask_("t1"), ["inv_001"]);
    expect(await screen.findByTestId("task-section-run-count-t1")).toHaveTextContent("1 run");
  });

  it("shows plural 'runs' for multiple invocations", async () => {
    renderSection(makeTask_("t1"), ["inv_001", "inv_002"]);
    expect(await screen.findByTestId("task-section-run-count-t1")).toHaveTextContent("2 runs");
  });

  it("shows 0 runs when invocationIds is empty", async () => {
    renderSection(makeTask_("t1"), []);
    expect(await screen.findByTestId("task-section-run-count-t1")).toHaveTextContent("0 runs");
  });

  it("renders InvocationGraph empty state when invocationIds is empty", async () => {
    renderSection(makeTask_("t1"), []);
    expect(await screen.findByTestId("invocation-graph-empty")).toBeInTheDocument();
  });

  it("renders its runs as a list by default, with a Graph tab that mounts the run graph", async () => {
    renderSection(makeTask_("t1"), ["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    expect(await screen.findByTestId("run-row-inv_001")).toBeInTheDocument();
    expect(screen.queryByTestId("invocation-graph")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Graph" }));
    expect(await screen.findByTestId("invocation-graph")).toBeInTheDocument();
  });

  it("shows cost roll-up when invocations have cost data", async () => {
    const task = makeTask_("t1");
    const doc = makeDocState({
      tasks: [makeTask("step", { input_tokens: 100, output_tokens: 50, total_cost_usd: 0.001 })],
    });
    renderSection(task, ["inv_001"], makeDocsState([["inv_001", doc]]));
    expect(await screen.findByTestId("task-section-cost-t1")).toHaveTextContent("$0.0010");
  });

  it("omits cost element when no cost data", async () => {
    renderSection(makeTask_("t1"), ["inv_001"], makeDocsState([["inv_001", makeDocState()]]));
    await screen.findByTestId("task-section-run-count-t1");
    expect(screen.queryByTestId("task-section-cost-t1")).not.toBeInTheDocument();
  });

  it("starts open with ▾ before the heading", async () => {
    renderSection(makeTask_("t1"), []);
    const details = await screen.findByTestId("task-section-t1");
    await expectDisclosureState(details, true);
    expect(disclosureSummary(details).children[1]).toBe(screen.getByTestId("task-section-heading-t1"));
  });

  it("closes and reopens on click, Enter and Space, hiding the run graph while closed", async () => {
    renderSection(makeTask_("t1"), []);
    const details = await screen.findByTestId("task-section-t1");
    await expectTogglesOnEveryActivation(details);
    activateDisclosure(details, "click");
    await expectDisclosureState(details, false);
    expect(screen.getByTestId("invocation-graph-empty")).not.toBeVisible();
  });

  it("keeps status, run count and cost in the summary beside the marker", async () => {
    const doc = makeDocState({
      tasks: [makeTask("step", { input_tokens: 100, output_tokens: 50, total_cost_usd: 0.001 })],
    });
    renderSection(makeTask_("t1"), ["inv_001"], makeDocsState([["inv_001", doc]]));
    const summary = disclosureSummary(await screen.findByTestId("task-section-t1"));
    expect(summary).toContainElement(screen.getByTestId("task-section-status-t1"));
    expect(summary).toContainElement(screen.getByTestId("task-section-run-count-t1"));
    expect(await within(summary).findByTestId("task-section-cost-t1")).toBeInTheDocument();
  });

  it("applies live nodeUpdates to cost roll-up", async () => {
    const task = makeTask_("t1");
    const doc = makeDocState({ tasks: [] });
    const live: LiveWorkspaceState = {
      invocations: {
        inv_001: {
          nodeUpdates: [makeTask("step", { input_tokens: 200, output_tokens: 80, total_cost_usd: 0.002 })],
          liveStreams: {},
        },
      },
      streamError: null,
      notices: [],
      stickyNotice: null,
    };
    renderSection(task, ["inv_001"], makeDocsState([["inv_001", doc]]), live);
    expect(await screen.findByTestId("task-section-cost-t1")).toHaveTextContent("$0.0020");
  });

  it("forwards readbacks to the inner InvocationGraph so the per-subtask DAG carries the badge", async () => {
    const task = makeTask_("t1");
    renderSection(
      task,
      ["inv_001"],
      makeDocsState([["inv_001", makeDocState()]]),
      LIVE_EMPTY,
      "",
      { inv_001: { status: "attached", text: "do it", origin: null } },
    );
    expect(await screen.findByTestId("instruction-badge-inv_001")).toHaveTextContent(
      "instructions attached",
    );
  });
});
