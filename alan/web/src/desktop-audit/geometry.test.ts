import { afterEach, describe, expect, it } from "vitest";

import { extractGeometryTree } from "./geometry";

function setRect(el: HTMLElement, rect: { x?: number; y?: number; width?: number; height?: number }): void {
  const resolved = { x: 0, y: 0, width: 0, height: 0, ...rect };
  el.getBoundingClientRect = () => ({
    ...resolved,
    top: resolved.y,
    left: resolved.x,
    right: resolved.x + resolved.width,
    bottom: resolved.y + resolved.height,
    toJSON: () => resolved,
  });
}

function el(tag: string, opts: { className?: string; text?: string; style?: string } = {}): HTMLElement {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.style) node.setAttribute("style", opts.style);
  if (opts.text) node.textContent = opts.text;
  setRect(node, { width: 0, height: 0 });
  return node;
}

afterEach(() => {
  document.body.innerHTML = "";
});

function mount(child: HTMLElement): HTMLElement {
  const container = document.createElement("div");
  container.appendChild(child);
  document.body.appendChild(container);
  return child;
}

describe("extractGeometryTree — action groups", () => {
  it("records a flex-row container with 2+ visible children as an action-group candidate", () => {
    const row = el("div", { style: "display: flex; flex-direction: row; column-gap: 8px" });
    setRect(row, { x: 0, y: 0, width: 300, height: 40 });
    const a = el("button", { className: "a", text: "Build" });
    setRect(a, { x: 0, y: 0, width: 60, height: 40 });
    const b = el("button", { className: "b", text: "Explore" });
    setRect(b, { x: 68, y: 0, width: 70, height: 40 });
    row.append(a, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });

    expect(tree.actionGroups).toHaveLength(1);
    const group = tree.actionGroups[0]!;
    expect(group.contentWidthPx).toBe(300);
    expect(group.columnGapPx).toBe(8);
    expect(group.children).toHaveLength(2);
    expect(group.children[0]!.rect).toEqual({ x: 0, y: 0, width: 60, height: 40 });
    expect(group.children[0]!.signature).toBe("button.a");
  });

  it("sorts multiple classes alphabetically in the signature", () => {
    const row = el("div", { style: "display: flex" });
    setRect(row, { width: 100, height: 20 });
    const a = el("span", { className: "zeta alpha", text: "x" });
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(a, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups[0]!.children[0]!.signature).toBe("span.alpha.zeta");
  });

  it("excludes padding and border from contentWidthPx", () => {
    const row = el("div", { style: "display: flex; padding: 10px; border: 5px solid black" });
    setRect(row, { width: 340, height: 40 });
    const a = el("button", { text: "Build" });
    setRect(a, { width: 60, height: 40 });
    const b = el("button", { text: "Explore" });
    setRect(b, { width: 70, height: 40 });
    row.append(a, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups[0]!.contentWidthPx).toBe(310);
  });

  it("reads column-gap when set, and defaults to 0 when unset", () => {
    const row = el("div", { style: "display: flex; column-gap: 4px" });
    setRect(row, { width: 100, height: 20 });
    const a = el("span", { text: "x" });
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(a, b);
    mount(row);

    const withGap = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(withGap.actionGroups[0]!.columnGapPx).toBe(4);
    document.body.innerHTML = "";

    const rowNoGap = el("div", { style: "display: flex" });
    setRect(rowNoGap, { width: 100, height: 20 });
    const c = el("span", { text: "x" });
    setRect(c, { width: 10, height: 10 });
    const d = el("span", { text: "y" });
    setRect(d, { width: 10, height: 10 });
    rowNoGap.append(c, d);
    mount(rowNoGap);
    const withoutGap = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(withoutGap.actionGroups[0]!.columnGapPx).toBe(0);
  });

  it("treats an unset flex-direction as row (the CSS initial value)", () => {
    const row = el("div", { style: "display: flex" });
    setRect(row, { width: 100, height: 20 });
    const a = el("span", { text: "x" });
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(a, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups).toHaveLength(1);
  });

  it("counts row-reverse as a row container", () => {
    const row = el("div", { style: "display: flex; flex-direction: row-reverse" });
    setRect(row, { width: 100, height: 20 });
    const a = el("span", { text: "x" });
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(a, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups).toHaveLength(1);
  });

  it("excludes column and column-reverse flex containers", () => {
    const col = el("div", { style: "display: flex; flex-direction: column" });
    setRect(col, { width: 100, height: 80 });
    const a = el("span", { text: "x" });
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    col.append(a, b);
    mount(col);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups).toHaveLength(0);
  });

  it("excludes non-flex containers even with multiple visible children", () => {
    const block = el("div", { style: "display: block" });
    setRect(block, { width: 100, height: 80 });
    const a = el("span", { text: "x" });
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    block.append(a, b);
    mount(block);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups).toHaveLength(0);
  });

  it("excludes a flex-row container with fewer than 2 visible children", () => {
    const row = el("div", { style: "display: flex; flex-direction: row" });
    setRect(row, { width: 100, height: 20 });
    const a = el("span", { text: "x" });
    setRect(a, { width: 10, height: 10 });
    const hidden = el("span", { text: "y" });
    setRect(hidden, { width: 0, height: 0 });
    row.append(a, hidden);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups).toHaveLength(0);
  });

  it("builds a dotted ancestry path joined by '>' for a nested action group", () => {
    const outer = el("section", { className: "outer" });
    setRect(outer, { width: 400, height: 100 });
    const row = el("div", { className: "row", style: "display: flex" });
    setRect(row, { width: 400, height: 40 });
    const a = el("span", { text: "x" });
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(a, b);
    outer.append(row);
    mount(outer);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups[0]!.path).toContain("section.outer > div.row");
  });
});

describe("extractGeometryTree — repeating groups", () => {
  function makeRow(name: string, cellText: string): HTMLElement {
    const row = el("tr", { className: "row" });
    setRect(row, { x: 0, y: 0, width: 200, height: 20 });
    const nameCell = el("td", { className: "name", text: name });
    setRect(nameCell, { x: 0, y: 0, width: 100, height: 20 });
    const otherCell = el("td", { className: "other", text: cellText });
    setRect(otherCell, { x: 100, y: 0, width: 100, height: 20 });
    row.append(nameCell, otherCell);
    return row;
  }

  it("groups same-signature siblings once the group reaches minRepeatingGroupSize", () => {
    const tbody = el("tbody");
    setRect(tbody, { width: 200, height: 60 });
    tbody.append(makeRow("alpha", "a"), makeRow("beta", "b"), makeRow("gamma", "c"));
    mount(tbody);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.repeatingGroups).toHaveLength(1);
    const group = tree.repeatingGroups[0]!;
    expect(group.rows).toHaveLength(3);
    expect(group.rows[0]).toHaveLength(2);
    expect(group.rows[0]![0]!.signature).toBe("td.name");
  });

  it("does not report a group under the minimum size", () => {
    const tbody = el("tbody");
    setRect(tbody, { width: 200, height: 40 });
    tbody.append(makeRow("alpha", "a"), makeRow("beta", "b"));
    mount(tbody);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.repeatingGroups).toHaveLength(0);
  });
});

describe("extractGeometryTree — firstTextRect", () => {
  it("returns null firstTextRect for an element with no text-bearing descendant", () => {
    const row = el("div", { style: "display: flex" });
    setRect(row, { width: 100, height: 20 });
    const a = el("span");
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(a, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups[0]!.children[0]!.firstTextRect).toBeNull();
    expect(tree.actionGroups[0]!.children[1]!.firstTextRect).not.toBeNull();
  });

  it("finds text nested several levels below the action-group child", () => {
    const row = el("div", { style: "display: flex" });
    setRect(row, { width: 100, height: 20 });
    const wrapper = el("span");
    setRect(wrapper, { width: 40, height: 20 });
    const inner = el("a", { text: "click me" });
    setRect(inner, { x: 5, y: 2, width: 30, height: 16 });
    wrapper.append(inner);
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(wrapper, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups[0]!.children[0]!.firstTextRect).toEqual({ x: 5, y: 2, width: 30, height: 16 });
  });

  it("treats a whitespace-only text node as carrying no own text", () => {
    const row = el("div", { style: "display: flex" });
    setRect(row, { width: 100, height: 20 });
    const whitespaceOnly = el("span");
    whitespaceOnly.textContent = "   \n  ";
    setRect(whitespaceOnly, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(whitespaceOnly, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups[0]!.children[0]!.firstTextRect).toBeNull();
  });

  it("skips a first child with no text before finding text in a later sibling", () => {
    const row = el("div", { style: "display: flex" });
    setRect(row, { width: 100, height: 20 });
    const wrapper = el("span");
    setRect(wrapper, { width: 40, height: 20 });
    const emptyFirst = el("i");
    setRect(emptyFirst, { width: 5, height: 5 });
    const textSecond = el("b", { text: "found me" });
    setRect(textSecond, { x: 9, y: 3, width: 20, height: 10 });
    wrapper.append(emptyFirst, textSecond);
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(wrapper, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3 });
    expect(tree.actionGroups[0]!.children[0]!.firstTextRect).toEqual({ x: 9, y: 3, width: 20, height: 10 });
  });
});

describe("extractGeometryTree — root argument", () => {
  it("accepts an Element root directly, not only Document", () => {
    const row = el("div", { style: "display: flex" });
    setRect(row, { width: 100, height: 20 });
    const a = el("span", { text: "x" });
    setRect(a, { width: 10, height: 10 });
    const b = el("span", { text: "y" });
    setRect(b, { width: 10, height: 10 });
    row.append(a, b);
    mount(row);

    const tree = extractGeometryTree({ minRepeatingGroupSize: 3, root: row.parentElement! });
    expect(tree.actionGroups).toHaveLength(1);
  });
});
