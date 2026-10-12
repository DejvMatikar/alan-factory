import { describe, expect, it, vi } from "vitest";

import type { WorkflowTask } from "../../../api";
import {
  buildGroupEdges,
  buildGroupNodes,
  buildGroupYPositions,
  buildStepChildren,
  buildStepEdges,
  computeGroupDimensions,
  type GroupDims,
  type GroupInfo,
} from "./invocationLayout";
import {
  COL_WIDTH,
  GROUP_GAP,
  GROUP_PADDING,
  HEADER_HEIGHT,
  NODE_WIDTH,
  ROW_HEIGHT,
} from "./workflow";

function makeTask(id: string, overrides: Partial<WorkflowTask> = {}): WorkflowTask {
  return {
    id,
    type: "io.alan.LLMStep",
    role: id,
    model: "sonnet",
    depends_on: [],
    status: "success",
    started_at: null,
    completed_at: null,
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

function makeGroupInfo(invId: string, overrides: Partial<GroupInfo> = {}): GroupInfo {
  return {
    invId,
    team: "research",
    status: "success",
    duration: null,
    stepCount: 2,
    expanded: false,
    totalCostUsd: null,
    inputTokens: null,
    outputTokens: null,
    ...overrides,
  };
}

describe("computeGroupDimensions", () => {
  it("returns minimum size for empty task list", () => {
    const dims = computeGroupDimensions([]);
    expect(dims.width).toBe(COL_WIDTH + 2 * GROUP_PADDING);
    expect(dims.height).toBe(HEADER_HEIGHT + ROW_HEIGHT + 2 * GROUP_PADDING);
  });

  it("sizes correctly for one task", () => {
    const dims = computeGroupDimensions([makeTask("a")]);
    expect(dims.width).toBe(COL_WIDTH + 2 * GROUP_PADDING);
    expect(dims.height).toBe(HEADER_HEIGHT + ROW_HEIGHT + 2 * GROUP_PADDING);
  });

  it("adds extra row height for two parallel tasks (same column)", () => {
    const dims = computeGroupDimensions([makeTask("a"), makeTask("b")]);
    expect(dims.height).toBe(HEADER_HEIGHT + 2 * ROW_HEIGHT + 2 * GROUP_PADDING);
  });

  it("grows the group by exactly ROW_HEIGHT for every extra stacked task", () => {
    const one = computeGroupDimensions([makeTask("a")]);
    const three = computeGroupDimensions([makeTask("a"), makeTask("b"), makeTask("c")]);
    expect(three.height - one.height).toBe(2 * ROW_HEIGHT);
  });

  it("adds extra column width for sequential tasks", () => {
    const dims = computeGroupDimensions([
      makeTask("a"),
      makeTask("b", { depends_on: ["a"] }),
    ]);
    expect(dims.width).toBe(2 * COL_WIDTH + 2 * GROUP_PADDING);
  });
});

describe("buildGroupYPositions", () => {
  it("places first group at y=0", () => {
    const dims = new Map<string, GroupDims>([["inv_001", { width: 300, height: 200 }]]);
    const positions = buildGroupYPositions(["inv_001"], dims, new Set());
    expect(positions.get("inv_001")).toBe(0);
  });

  it("places second group using collapsed height plus gap when first is collapsed", () => {
    const dims = new Map<string, GroupDims>([
      ["inv_001", { width: 300, height: 200 }],
      ["inv_002", { width: 200, height: 150 }],
    ]);
    const positions = buildGroupYPositions(["inv_001", "inv_002"], dims, new Set());
    expect(positions.get("inv_002")).toBe(HEADER_HEIGHT + 2 * GROUP_PADDING + GROUP_GAP);
  });

  it("places second group using full height plus gap when first is expanded", () => {
    const dims = new Map<string, GroupDims>([
      ["inv_001", { width: 300, height: 200 }],
      ["inv_002", { width: 200, height: 150 }],
    ]);
    const positions = buildGroupYPositions(["inv_001", "inv_002"], dims, new Set(["inv_001"]));
    expect(positions.get("inv_002")).toBe(200 + GROUP_GAP);
  });

  it("falls back to collapsed height when dims key is missing for a collapsed group", () => {
    const positions = buildGroupYPositions(["inv_001", "inv_002"], new Map(), new Set());
    expect(positions.get("inv_001")).toBe(0);
    expect(positions.get("inv_002")).toBe(HEADER_HEIGHT + 2 * GROUP_PADDING + GROUP_GAP);
  });

  it("falls back to zero height when dims key is missing for an expanded group", () => {
    const positions = buildGroupYPositions(["inv_001", "inv_002"], new Map(), new Set(["inv_001"]));
    expect(positions.get("inv_001")).toBe(0);
    expect(positions.get("inv_002")).toBe(0 + GROUP_GAP);
  });
});

describe("buildGroupNodes", () => {
  it("produces one node per group info with collapsed height by default", () => {
    const dims = new Map<string, GroupDims>([["inv_001", { width: 300, height: 200 }]]);
    const yPositions = new Map<string, number>([["inv_001", 0]]);
    const onToggle = vi.fn();
    const nodes = buildGroupNodes([makeGroupInfo("inv_001")], yPositions, dims, onToggle, "t1");
    expect(nodes).toHaveLength(1);
    expect(nodes[0]!.id).toBe("inv_001");
    expect(nodes[0]!.type).toBe("invGroup");
    expect(nodes[0]!.position).toEqual({ x: 0, y: 0 });
    expect(nodes[0]!.style).toEqual({ width: 300, height: HEADER_HEIGHT + 2 * GROUP_PADDING });
  });

  it("uses full height when expanded", () => {
    const dims = new Map<string, GroupDims>([["inv_001", { width: 300, height: 200 }]]);
    const yPositions = new Map<string, number>([["inv_001", 0]]);
    const nodes = buildGroupNodes([makeGroupInfo("inv_001", { expanded: true })], yPositions, dims, vi.fn(), "t1");
    expect(nodes[0]!.style).toEqual({ width: 300, height: 200 });
  });

  it("data fields match GroupInfo plus onToggle", () => {
    const dims = new Map<string, GroupDims>([["inv_001", { width: 300, height: 200 }]]);
    const yPositions = new Map<string, number>([["inv_001", 50]]);
    const onToggle = vi.fn();
    const info = makeGroupInfo("inv_001", { team: "arch", status: "running", expanded: true });
    const nodes = buildGroupNodes([info], yPositions, dims, onToggle, "t1");
    const data = nodes[0]!.data;
    expect(data.team).toBe("arch");
    expect(data.status).toBe("running");
    expect(data.expanded).toBe(true);
    expect(data.onToggle).toBe(onToggle);
  });

  it("carries the owning task id in every group node's data", () => {
    const dims = new Map<string, GroupDims>([
      ["inv_001", { width: 300, height: 200 }],
      ["inv_002", { width: 300, height: 200 }],
    ]);
    const yPositions = new Map<string, number>([["inv_001", 0], ["inv_002", 300]]);
    const nodes = buildGroupNodes(
      [makeGroupInfo("inv_001"), makeGroupInfo("inv_002")],
      yPositions,
      dims,
      vi.fn(),
      "task-42",
    );
    expect(nodes.map((n) => n.data.taskId)).toEqual(["task-42", "task-42"]);
    expect(nodes.map((n) => n.data.invId)).toEqual(["inv_001", "inv_002"]);
  });

  it("falls back to zero dims when key missing in dims map", () => {
    const yPositions = new Map<string, number>([["inv_001", 0]]);
    const nodes = buildGroupNodes([makeGroupInfo("inv_001")], yPositions, new Map(), vi.fn(), "t1");
    expect(nodes[0]!.style).toEqual({ width: 0, height: HEADER_HEIGHT + 2 * GROUP_PADDING });
  });

  it("falls back to y=0 when key missing in yPositions map", () => {
    const dims = new Map<string, GroupDims>([["inv_001", { width: 300, height: 200 }]]);
    const nodes = buildGroupNodes([makeGroupInfo("inv_001")], new Map(), dims, vi.fn(), "t1");
    expect(nodes[0]!.position).toEqual({ x: 0, y: 0 });
  });
});

describe("buildStepChildren", () => {
  it("creates composite id and type for each task", () => {
    const nodes = buildStepChildren("inv_001", [makeTask("a")], null, vi.fn(), vi.fn());
    expect(nodes).toHaveLength(1);
    expect(nodes[0]!.id).toBe("inv_001::a");
    expect(nodes[0]!.type).toBe("step");
  });

  it("sets parentId and extent=parent", () => {
    const nodes = buildStepChildren("inv_001", [makeTask("a")], null, vi.fn(), vi.fn());
    expect(nodes[0]!.parentId).toBe("inv_001");
    expect(nodes[0]!.extent).toBe("parent");
  });

  it("applies y offset for header and padding", () => {
    const nodes = buildStepChildren("inv_001", [makeTask("a")], null, vi.fn(), vi.fn());
    expect(nodes[0]!.position.y).toBe(HEADER_HEIGHT + GROUP_PADDING);
  });

  it("marks task as selected when stepId matches", () => {
    const nodes = buildStepChildren("inv_001", [makeTask("a")], "a", vi.fn(), vi.fn());
    const data = nodes[0]!.data;
    expect(data.selected).toBe(true);
  });

  it("marks task as unselected when stepId does not match", () => {
    const nodes = buildStepChildren("inv_001", [makeTask("a")], "b", vi.fn(), vi.fn());
    const data = nodes[0]!.data;
    expect(data.selected).toBe(false);
  });

  it("marks every step node as not focusable so the wrapper adds no tab stop", () => {
    const nodes = buildStepChildren("inv_001", [makeTask("a"), makeTask("b")], null, vi.fn(), vi.fn());
    expect(nodes.map((n) => n.focusable)).toEqual([false, false]);
  });

  it("spaces stacked step nodes by ROW_HEIGHT and sizes each to NODE_WIDTH by ROW_HEIGHT", () => {
    const nodes = buildStepChildren("inv_001", [makeTask("a"), makeTask("b")], null, vi.fn(), vi.fn());
    expect(nodes[1]!.position.y - nodes[0]!.position.y).toBe(ROW_HEIGHT);
    expect(nodes[0]!.initialWidth).toBe(NODE_WIDTH);
    expect(nodes[0]!.initialHeight).toBe(ROW_HEIGHT);
  });

  it("sets testIdPrefix to invId", () => {
    const nodes = buildStepChildren("inv_001", [makeTask("a")], null, vi.fn(), vi.fn());
    const data = nodes[0]!.data;
    expect(data.testIdPrefix).toBe("inv_001");
  });

  it("onSelect closure passes invId to onSelectStep", () => {
    const onSelectStep = vi.fn();
    const nodes = buildStepChildren("inv_001", [makeTask("a")], null, onSelectStep, vi.fn());
    const data = nodes[0]!.data;
    (data.onSelect as (id: string) => void)("a");
    expect(onSelectStep).toHaveBeenCalledWith("inv_001", "a");
  });

  it("onOpenModal closure prepends invId to outer handler call", () => {
    const onOpenModal = vi.fn();
    const nodes = buildStepChildren("inv_001", [makeTask("a")], null, vi.fn(), onOpenModal);
    const data = nodes[0]!.data;
    (data.onOpenModal as (nodeId: string, tab: string) => void)("a", "output");
    expect(onOpenModal).toHaveBeenCalledWith("inv_001", "a", "output");
  });
});

describe("buildGroupEdges", () => {
  it("returns no edges for empty list", () => {
    expect(buildGroupEdges([])).toHaveLength(0);
  });

  it("returns no edges for single invocation", () => {
    expect(buildGroupEdges(["inv_001"])).toHaveLength(0);
  });

  it("creates one chain edge for two invocations", () => {
    const edges = buildGroupEdges(["inv_001", "inv_002"]);
    expect(edges).toHaveLength(1);
    expect(edges[0]!.source).toBe("inv_001");
    expect(edges[0]!.target).toBe("inv_002");
  });

  it("sets arrowclosed marker on chain edges", () => {
    const edges = buildGroupEdges(["inv_001", "inv_002"]);
    expect(edges[0]!.markerEnd).toMatchObject({ type: "arrowclosed" });
  });
});

describe("buildStepEdges", () => {
  it("returns no edges when tasks have no deps", () => {
    expect(buildStepEdges("inv_001", [makeTask("a")])).toHaveLength(0);
  });

  it("creates scoped edge for a dependency", () => {
    const edges = buildStepEdges("inv_001", [
      makeTask("a"),
      makeTask("b", { depends_on: ["a"] }),
    ]);
    expect(edges).toHaveLength(1);
    expect(edges[0]!.source).toBe("inv_001::a");
    expect(edges[0]!.target).toBe("inv_001::b");
  });

  it("creates multiple edges for multiple deps", () => {
    const edges = buildStepEdges("inv_001", [
      makeTask("a"),
      makeTask("b"),
      makeTask("c", { depends_on: ["a", "b"] }),
    ]);
    expect(edges).toHaveLength(2);
  });

  it("sets arrowclosed marker on step edges", () => {
    const edges = buildStepEdges("inv_001", [
      makeTask("a"),
      makeTask("b", { depends_on: ["a"] }),
    ]);
    expect(edges[0]!.markerEnd).toMatchObject({ type: "arrowclosed" });
  });
});
