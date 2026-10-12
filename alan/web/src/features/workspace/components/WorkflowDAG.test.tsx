import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowTask } from "../../../api";
import WorkflowDAG, { StepNodeCard, computeDuration } from "./WorkflowDAG";

const graph = vi.hoisted(() => ({ nodes: [] as { id: string; focusable?: boolean }[] }));

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
  }: {
    nodes: { id: string; type: string; focusable?: boolean; data: Record<string, unknown> }[];
    nodeTypes: Record<
      string,
      React.ComponentType<{ data: Record<string, unknown> }>
    >;
  }) {
    graph.nodes = nodes;
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

function makeTask(id: string, overrides: Partial<WorkflowTask> = {}): WorkflowTask {
  return {
    id,
    type: "io.alan.LLMStep",
    role: id,
    model: "sonnet",
    depends_on: [],
    status: "success",
    started_at: "2026-05-12T08:00:00Z",
    completed_at: "2026-05-12T08:00:10Z",
    stream_path: null,
    dispatched: [],
    error: null,
    input_tokens: null,
    output_tokens: null,
    cache_creation_input_tokens: null,
    cache_read_input_tokens: null,
    total_cost_usd: null,
    usage_raw: null,
    ...overrides,
  };
}

function renderCard(
  task: WorkflowTask,
  props: { selected?: boolean; onSelect?: () => void; onOpenModal?: () => void; testIdPrefix?: string } = {},
) {
  return render(
    <StepNodeCard
      task={task}
      selected={props.selected ?? false}
      onSelect={props.onSelect ?? vi.fn()}
      onOpenModal={props.onOpenModal ?? vi.fn()}
      testIdPrefix={props.testIdPrefix}
    />,
  );
}

function mainButton(name: string) {
  return screen.getByRole("button", { name: `Show activity of ${name}` });
}

describe("computeDuration", () => {
  it("returns null when started_at is null", () => {
    expect(computeDuration(makeTask("a", { started_at: null, completed_at: null }))).toBeNull();
  });

  it("returns null when start is NaN", () => {
    expect(computeDuration(makeTask("a", { started_at: "bad" }))).toBeNull();
  });

  it("returns running… when completed_at is null", () => {
    expect(
      computeDuration(makeTask("a", { completed_at: null })),
    ).toBe("running…");
  });

  it("returns running… when completed_at is an invalid date string", () => {
    expect(
      computeDuration(makeTask("a", { completed_at: "not-a-date" })),
    ).toBe("running…");
  });

  it("returns elapsed seconds", () => {
    expect(computeDuration(makeTask("a"))).toBe("10s");
  });

  it("returns 0s when end equals start", () => {
    expect(
      computeDuration(
        makeTask("a", {
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:00Z",
        }),
      ),
    ).toBe("0s");
  });
});

describe("StepNodeCard", () => {
  it("renders with no testIdPrefix", () => {
    renderCard(makeTask("writer"));
    expect(screen.getByTestId("workflow-node-writer")).toBeInTheDocument();
  });

  it("renders with testIdPrefix", () => {
    renderCard(makeTask("writer"), { testIdPrefix: "inv_001" });
    expect(screen.getByTestId("workflow-node-inv_001-writer")).toBeInTheDocument();
  });

  it("names the main button after what clicking does", () => {
    renderCard(makeTask("writer"));
    expect(mainButton("writer")).toBeInTheDocument();
  });

  it("adds the step id to the name when the role differs from it", () => {
    renderCard(makeTask("step_2", { role: "reviewer" }));
    expect(mainButton("reviewer (step_2)")).toBeInTheDocument();
  });

  it("marks the selected card with aria-current and leaves the others unmarked", () => {
    render(
      <>
        <StepNodeCard task={makeTask("a")} selected={true} onSelect={vi.fn()} onOpenModal={vi.fn()} />
        <StepNodeCard task={makeTask("b")} selected={false} onSelect={vi.fn()} onOpenModal={vi.fn()} />
      </>,
    );
    expect(mainButton("a")).toHaveAttribute("aria-current", "true");
    expect(mainButton("b")).not.toHaveAttribute("aria-current");
  });

  it("draws selection as a left-edge cue without a ring", () => {
    renderCard(makeTask("a"), { selected: true });
    const card = screen.getByTestId("workflow-node-a");
    expect(card.className).toContain("border-l-4");
    expect(card.className).toContain("border-text-primary");
    expect(card.className).not.toContain("ring-1");
  });

  it("draws no selection cue when unselected", () => {
    renderCard(makeTask("a"));
    const card = screen.getByTestId("workflow-node-a");
    expect(card.className).not.toContain("border-l-4");
    expect(card.className).not.toContain("ring-1");
  });

  it("uses the standard focus ring on the main button, distinct from the selected card's edge cue", () => {
    renderCard(makeTask("a"), { selected: true });
    const button = mainButton("a");
    expect(button.className).toContain("focus-visible:ring-2");
    expect(button.className).toContain("focus-visible:ring-text-primary/60");
    expect(button.className).not.toContain("border-l-4");
    expect(screen.getByTestId("workflow-node-a").className).not.toContain("ring-2");
  });

  it("shows a pointer cursor on the main button", () => {
    renderCard(makeTask("a"));
    expect(mainButton("a").className).toContain("cursor-pointer");
  });

  it("brightens the border on hover while idle", () => {
    renderCard(makeTask("a"));
    expect(screen.getByTestId("workflow-node-a").className).toContain("hover:border-text-muted");
  });

  it("shows a trailing chevron inside the main button when idle", () => {
    renderCard(makeTask("a"));
    expect(within(mainButton("a")).getByTestId("step-card-chevron")).toHaveAttribute("aria-hidden", "true");
  });

  it("keeps the fixed 220px card width", () => {
    renderCard(makeTask("a"));
    expect(screen.getByTestId("workflow-node-a")).toHaveStyle({ width: "220px" });
  });

  it("keeps the icons out of the main button but inside the card", () => {
    renderCard(makeTask("a"));
    expect(within(mainButton("a")).queryByTestId("icon-output")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("workflow-node-a")).getByTestId("icon-output")).toBeInTheDocument();
  });

  it("shows duration", () => {
    renderCard(makeTask("a"));
    expect(screen.getByText("10s")).toBeInTheDocument();
  });

  it("puts model and duration on one row", () => {
    renderCard(makeTask("a"));
    expect(screen.getByText("sonnet").parentElement).toBe(screen.getByText("10s").parentElement);
  });

  it("hides duration when started_at is null", () => {
    renderCard(makeTask("a", { started_at: null }));
    expect(screen.queryByText(/^\d+s$/)).not.toBeInTheDocument();
  });

  it("shows error text when failed", () => {
    renderCard(makeTask("a", { status: "failed", error: "boom" }));
    expect(screen.getByTestId("workflow-node-a-error")).toHaveTextContent("boom");
  });

  it("shows prefixed error testid when testIdPrefix provided", () => {
    renderCard(makeTask("worker", { status: "failed", error: "crashed" }), { testIdPrefix: "inv_001" });
    expect(screen.getByTestId("workflow-node-inv_001-worker-error")).toHaveTextContent("crashed");
  });

  it("carries no title on the card even when the step failed", () => {
    renderCard(makeTask("a", { status: "failed", error: "boom" }));
    expect(screen.getByTestId("workflow-node-a")).not.toHaveAttribute("title");
    expect(mainButton("a")).not.toHaveAttribute("title");
  });

  it("shows the clamped error preview inside the main button for a failed step", () => {
    renderCard(makeTask("a", { status: "failed", error: "boom" }));
    const preview = screen.getByTestId("workflow-node-a-error");
    expect(preview).toBeVisible();
    expect(preview.className).toContain("line-clamp-2");
    expect(mainButton("a")).toContainElement(preview);
  });

  it("describes the main button by both its status and its error", () => {
    renderCard(makeTask("a", { status: "failed", error: "boom" }));
    const ids = mainButton("a").getAttribute("aria-describedby")!.split(" ");
    expect(ids).toHaveLength(2);
    expect(document.getElementById(ids[0]!)).toHaveTextContent("failed");
    expect(document.getElementById(ids[1]!)).toHaveTextContent("boom");
  });

  it("describes the main button by its status alone when there is no error", () => {
    renderCard(makeTask("a"));
    const ids = mainButton("a").getAttribute("aria-describedby")!.split(" ");
    expect(ids).toHaveLength(1);
    expect(document.getElementById(ids[0]!)).toHaveTextContent("success");
  });

  it("hides error when status is not failed", () => {
    renderCard(makeTask("a", { status: "success", error: "ignored" }));
    expect(screen.queryByTestId("workflow-node-a-error")).not.toBeInTheDocument();
  });

  it("hides error when error is empty string", () => {
    renderCard(makeTask("a", { status: "failed", error: "" }));
    expect(screen.queryByTestId("workflow-node-a-error")).not.toBeInTheDocument();
  });

  it("hides error when error is null", () => {
    renderCard(makeTask("a", { status: "failed", error: null }));
    expect(screen.queryByTestId("workflow-node-a-error")).not.toBeInTheDocument();
  });

  it("calls onSelect with task id on click", async () => {
    const onSelect = vi.fn();
    renderCard(makeTask("a"), { onSelect });
    await userEvent.click(mainButton("a"));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("a");
  });

  it("selects on Enter from the keyboard", async () => {
    const onSelect = vi.fn();
    renderCard(makeTask("a"), { onSelect });
    mainButton("a").focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("a");
  });

  it("selects on Space from the keyboard", async () => {
    const onSelect = vi.fn();
    renderCard(makeTask("a"), { onSelect });
    mainButton("a").focus();
    await userEvent.keyboard(" ");
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("a");
  });

  it("does not select on other keys", async () => {
    const onSelect = vi.fn();
    renderCard(makeTask("a"), { onSelect });
    mainButton("a").focus();
    await userEvent.keyboard("x");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("does not select when an icon is clicked", async () => {
    const onSelect = vi.fn();
    renderCard(makeTask("a"), { onSelect });
    await userEvent.click(screen.getByTestId("icon-output"));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("clicking OutputIcon calls onOpenModal with output tab", async () => {
    const onOpenModal = vi.fn();
    renderCard(makeTask("a"), { onOpenModal });
    await userEvent.click(screen.getByTestId("icon-output"));
    expect(onOpenModal).toHaveBeenCalledWith("a", "output");
  });

  it("clicking PromptIcon calls onOpenModal with prompt tab", async () => {
    const onOpenModal = vi.fn();
    renderCard(makeTask("a"), { onOpenModal });
    await userEvent.click(screen.getByTestId("icon-prompt"));
    expect(onOpenModal).toHaveBeenCalledWith("a", "prompt");
  });

  it("clicking ConfigIcon calls onOpenModal with config tab", async () => {
    const onOpenModal = vi.fn();
    renderCard(makeTask("a"), { onOpenModal });
    await userEvent.click(screen.getByTestId("icon-config"));
    expect(onOpenModal).toHaveBeenCalledWith("a", "config");
  });

  it("names each icon after the step it belongs to", () => {
    renderCard(makeTask("step_2", { role: "reviewer" }));
    expect(screen.getByRole("button", { name: "View output of reviewer (step_2)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View prompt of reviewer (step_2)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View config of reviewer (step_2)" })).toBeInTheDocument();
  });

  it("renders usage row when input_tokens and output_tokens are present", () => {
    renderCard(makeTask("a", { input_tokens: 100, output_tokens: 250 }));
    expect(screen.getByTestId("workflow-node-a-usage")).toHaveTextContent("100↑ 250↓");
  });

  it("renders usage row with cost when total_cost_usd is present", () => {
    renderCard(makeTask("a", { input_tokens: 42, output_tokens: 88, total_cost_usd: 0.0007 }));
    expect(screen.getByTestId("workflow-node-a-usage")).toHaveTextContent("42↑ 88↓ · $0.0007");
  });

  it("renders usage testid with prefix when testIdPrefix is provided", () => {
    renderCard(makeTask("writer", { input_tokens: 10, output_tokens: 20 }), { testIdPrefix: "inv_001" });
    expect(screen.getByTestId("workflow-node-inv_001-writer-usage")).toBeInTheDocument();
  });

  it("hides usage row when both token fields are null", () => {
    renderCard(makeTask("a", { input_tokens: null, output_tokens: null }));
    expect(screen.queryByTestId("workflow-node-a-usage")).not.toBeInTheDocument();
  });
});

describe("WorkflowDAG", () => {
  it("renders graph container and nodes", () => {
    render(
      <WorkflowDAG
        tasks={[makeTask("a")]}
        selectedId={null}
        onSelect={vi.fn()}
        onOpenModal={vi.fn()}
        testId="wf-dag"
      />,
    );
    expect(screen.getByTestId("wf-dag")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-node-a")).toBeInTheDocument();
  });

  it("marks the selected node and only that node", () => {
    render(
      <WorkflowDAG
        tasks={[makeTask("a"), makeTask("b")]}
        selectedId="a"
        onSelect={vi.fn()}
        onOpenModal={vi.fn()}
        testId="wf-dag"
      />,
    );
    expect(mainButton("a")).toHaveAttribute("aria-current", "true");
    expect(mainButton("b")).not.toHaveAttribute("aria-current");
  });

  it("builds graph nodes that are not focusable wrappers", () => {
    render(
      <WorkflowDAG
        tasks={[makeTask("a"), makeTask("b")]}
        selectedId={null}
        onSelect={vi.fn()}
        onOpenModal={vi.fn()}
        testId="wf-dag"
      />,
    );
    expect(graph.nodes.map((n) => n.focusable)).toEqual([false, false]);
  });
});
