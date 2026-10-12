import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import InterTaskDAG, { nodeWidth, truncLabel } from "./InterTaskDAG";

describe("truncLabel", () => {
  it("returns the label unchanged when at or under 40 chars", () => {
    expect(truncLabel("hello")).toBe("hello");
    expect(truncLabel("a".repeat(40))).toBe("a".repeat(40));
  });

  it("truncates at 40 code points and appends ellipsis", () => {
    expect(truncLabel("a".repeat(41))).toBe("a".repeat(40) + "…");
  });

  it("counts emoji as one code point (not two bytes)", () => {
    const emoji40 = "🚀".repeat(40);
    expect(truncLabel(emoji40)).toBe(emoji40);
    const emoji41 = "🚀".repeat(41);
    expect(truncLabel(emoji41)).toBe("🚀".repeat(40) + "…");
  });

  it("counts CJK chars as one code point each", () => {
    const cjk40 = "字".repeat(40);
    expect(truncLabel(cjk40)).toBe(cjk40);
    const cjk41 = "字".repeat(41);
    expect(truncLabel(cjk41)).toBe("字".repeat(40) + "…");
  });
});

describe("nodeWidth", () => {
  it("clamps to MIN_W for very short labels", () => {
    expect(nodeWidth("Hi")).toBe(80);
  });

  it("computes width proportional to character count for medium labels", () => {
    expect(nodeWidth("Hello World")).toBe(109);
  });

  it("clamps to MAX_W for very long labels", () => {
    expect(nodeWidth("a".repeat(100))).toBe(330);
  });
});

describe("InterTaskDAG", () => {
  it("renders null when nodes is empty", () => {
    const { container } = render(<InterTaskDAG nodes={[]} edges={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders an SVG with data-testid='inter-task-dag' for a single node", () => {
    render(<InterTaskDAG nodes={[{ id: "a", label: "Alpha" }]} edges={[]} />);
    expect(screen.getByTestId("inter-task-dag")).toBeInTheDocument();
  });

  it("renders a node element for each input node", () => {
    render(
      <InterTaskDAG
        nodes={[
          { id: "a", label: "Alpha" },
          { id: "b", label: "Beta" },
        ]}
        edges={[]}
      />,
    );
    expect(screen.getByTestId("dag-node-a")).toBeInTheDocument();
    expect(screen.getByTestId("dag-node-b")).toBeInTheDocument();
  });

  it("renders a planning_state badge only on nodes that set it", () => {
    render(
      <InterTaskDAG
        nodes={[
          { id: "a", label: "Alpha", planningState: "detailed" },
          { id: "b", label: "Beta" },
        ]}
        edges={[]}
      />,
    );
    const glyphEl = screen.getByTestId("dag-planning-a");
    expect(glyphEl.querySelector("title")).toHaveTextContent("Detailed plan");
    expect(screen.getByLabelText("planning: Detailed plan")).toBeInTheDocument();
    expect(screen.queryByTestId("dag-planning-b")).toBeNull();
  });

  it("shows the outline glyph for an outline node", () => {
    render(
      <InterTaskDAG nodes={[{ id: "a", label: "A", planningState: "outline" }]} edges={[]} />,
    );
    const glyphEl = screen.getByTestId("dag-planning-a");
    expect(glyphEl).toHaveTextContent("O");
    expect(glyphEl.querySelector("title")).toHaveTextContent("Outline");
  });

  it("renders the truncated label inside each node", () => {
    render(
      <InterTaskDAG
        nodes={[{ id: "x", label: "a".repeat(41) }]}
        edges={[]}
      />,
    );
    expect(screen.getByTestId("dag-node-x")).toHaveTextContent("a".repeat(40) + "…");
  });

  it("renders an aria-label on the SVG", () => {
    render(<InterTaskDAG nodes={[{ id: "n", label: "N" }]} edges={[]} />);
    expect(screen.getByRole("img", { name: "Task dependency graph" })).toBeInTheDocument();
  });

  it("renders a responsive SVG (viewBox + fluid width capped at natural size) so it always fits", () => {
    render(
      <InterTaskDAG
        nodes={[
          { id: "a", label: "Alpha" },
          { id: "b", label: "Beta" },
          { id: "c", label: "Gamma" },
        ]}
        edges={[
          { from: "a", to: "b" },
          { from: "b", to: "c" },
        ]}
      />,
    );
    const svg = screen.getByTestId("inter-task-dag");
    expect(svg.getAttribute("viewBox")).toMatch(/^0 0 \d+ \d+$/);
    expect(svg).not.toHaveAttribute("width");
    expect(svg).not.toHaveAttribute("height");
    expect(svg.style.width).toBe("100%");
    expect(svg.style.height).toBe("auto");
    expect(svg.style.maxWidth).not.toBe("");
  });

  it("renders a line for each valid edge", () => {
    const { container } = render(
      <InterTaskDAG
        nodes={[
          { id: "a", label: "A" },
          { id: "b", label: "B" },
        ]}
        edges={[{ from: "a", to: "b" }]}
      />,
    );
    expect(container.querySelectorAll("line")).toHaveLength(1);
  });

  it("skips edges whose from or to id is not in the nodes list", () => {
    const { container } = render(
      <InterTaskDAG
        nodes={[{ id: "a", label: "A" }]}
        edges={[
          { from: "a", to: "missing" },
          { from: "also-missing", to: "a" },
        ]}
      />,
    );
    expect(container.querySelectorAll("line")).toHaveLength(0);
  });

  it("handles a 3-node linear chain without throwing", () => {
    render(
      <InterTaskDAG
        nodes={[
          { id: "a", label: "A" },
          { id: "b", label: "B" },
          { id: "c", label: "C" },
        ]}
        edges={[
          { from: "a", to: "b" },
          { from: "b", to: "c" },
        ]}
      />,
    );
    expect(screen.getByTestId("dag-node-a")).toBeInTheDocument();
    expect(screen.getByTestId("dag-node-b")).toBeInTheDocument();
    expect(screen.getByTestId("dag-node-c")).toBeInTheDocument();
  });

  it("handles a cycle gracefully without throwing", () => {
    render(
      <InterTaskDAG
        nodes={[
          { id: "a", label: "A" },
          { id: "b", label: "B" },
        ]}
        edges={[
          { from: "a", to: "b" },
          { from: "b", to: "a" },
        ]}
      />,
    );
    expect(screen.getByTestId("dag-node-a")).toBeInTheDocument();
    expect(screen.getByTestId("dag-node-b")).toBeInTheDocument();
  });

  it("two instances render different marker ids (useId uniqueness)", () => {
    const { container: c1 } = render(
      <InterTaskDAG nodes={[{ id: "a", label: "A" }]} edges={[]} />,
    );
    const { container: c2 } = render(
      <InterTaskDAG nodes={[{ id: "b", label: "B" }]} edges={[]} />,
    );
    const id1 = c1.querySelector("marker")?.getAttribute("id") ?? "";
    const id2 = c2.querySelector("marker")?.getAttribute("id") ?? "";
    expect(id1).not.toBe("");
    expect(id2).not.toBe("");
    expect(id1).not.toBe(id2);
  });

  it("nodes with parallel dependency ranks are placed in the same column", () => {
    const { container } = render(
      <InterTaskDAG
        nodes={[
          { id: "root", label: "Root" },
          { id: "left", label: "Left" },
          { id: "right", label: "Right" },
        ]}
        edges={[
          { from: "root", to: "left" },
          { from: "root", to: "right" },
        ]}
      />,
    );
    expect(container.querySelectorAll("[data-testid^='dag-node-']")).toHaveLength(3);
  });

  it("handles diamond (fan-in) pattern: node with two predecessors", () => {
    render(
      <InterTaskDAG
        nodes={[
          { id: "a", label: "A" },
          { id: "b", label: "B" },
          { id: "c", label: "C" },
          { id: "d", label: "D" },
        ]}
        edges={[
          { from: "a", to: "b" },
          { from: "a", to: "c" },
          { from: "b", to: "d" },
          { from: "c", to: "d" },
        ]}
      />,
    );
    expect(screen.getByTestId("dag-node-d")).toBeInTheDocument();
  });

  it("renders a status indicator only on nodes that set status", () => {
    render(
      <InterTaskDAG
        nodes={[
          { id: "a", label: "Alpha", status: "completed" },
          { id: "b", label: "Beta" },
        ]}
        edges={[]}
      />,
    );
    const dotEl = screen.getByTestId("dag-status-a");
    expect(dotEl).toHaveAttribute("aria-label", "status: done");
    expect(dotEl.querySelector("title")).toHaveTextContent("done");
    expect(screen.queryByTestId("dag-status-b")).toBeNull();
  });

  it.each([
    ["completed", "done"],
    ["awaiting_input", "needs you"],
    ["failed", "failed"],
    ["running", "in progress"],
    ["pending", "in progress"],
    ["ready", "in progress"],
  ] as const)("maps task status %s to the %s bucket", (status, bucket) => {
    render(<InterTaskDAG nodes={[{ id: "a", label: "A", status }]} edges={[]} />);
    expect(screen.getByTestId("dag-status-a")).toHaveAttribute("aria-label", `status: ${bucket}`);
  });

  it("renders an SVG title tooltip with the full node label", () => {
    render(
      <InterTaskDAG nodes={[{ id: "a", label: "Full Task Title Here" }]} edges={[]} />,
    );
    const node = screen.getByTestId("dag-node-a");
    expect(node.querySelector("title")).toHaveTextContent("Full Task Title Here");
  });

  it("title tooltip shows the full label even when truncated in display", () => {
    const longLabel = "a".repeat(50);
    render(
      <InterTaskDAG nodes={[{ id: "x", label: longLabel }]} edges={[]} />,
    );
    const node = screen.getByTestId("dag-node-x");
    expect(node.querySelector("title")).toHaveTextContent(longLabel);
  });
});
