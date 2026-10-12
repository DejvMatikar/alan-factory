import { describe, expect, it } from "vitest";

import { buildTaskRanks, topoSortTasks } from "./taskSort";

describe("topoSortTasks", () => {
  it("returns single task unchanged", () => {
    const tasks = [{ id: "a", depends_on: [] }];
    expect(topoSortTasks(tasks)).toEqual(tasks);
  });

  it("orders root before dependent", () => {
    const tasks = [
      { id: "b", depends_on: ["a"] },
      { id: "a", depends_on: [] },
    ];
    const sorted = topoSortTasks(tasks);
    const ids = sorted.map((t) => t.id);
    expect(ids.indexOf("a")).toBeLessThan(ids.indexOf("b"));
  });

  it("handles linear chain a -> b -> c", () => {
    const tasks = [
      { id: "c", depends_on: ["b"] },
      { id: "b", depends_on: ["a"] },
      { id: "a", depends_on: [] },
    ];
    const sorted = topoSortTasks(tasks);
    const ids = sorted.map((t) => t.id);
    expect(ids).toEqual(["a", "b", "c"]);
  });

  it("handles diamond: a -> b, a -> c, b -> d, c -> d", () => {
    const tasks = [
      { id: "d", depends_on: ["b", "c"] },
      { id: "b", depends_on: ["a"] },
      { id: "c", depends_on: ["a"] },
      { id: "a", depends_on: [] },
    ];
    const sorted = topoSortTasks(tasks);
    const ids = sorted.map((t) => t.id);
    expect(ids[0]).toBe("a");
    expect(ids[ids.length - 1]).toBe("d");
    expect(ids.indexOf("b")).toBeLessThan(ids.indexOf("d"));
    expect(ids.indexOf("c")).toBeLessThan(ids.indexOf("d"));
  });

  it("appends cycle nodes rather than dropping them", () => {
    const tasks = [
      { id: "a", depends_on: ["b"] },
      { id: "b", depends_on: ["a"] },
    ];
    const sorted = topoSortTasks(tasks);
    expect(sorted).toHaveLength(2);
    expect(sorted.map((t) => t.id).sort()).toEqual(["a", "b"]);
  });

  it("returns empty array for empty input", () => {
    expect(topoSortTasks([])).toEqual([]);
  });

  it("preserves task objects by reference", () => {
    const a = { id: "a", depends_on: [] as string[], extra: 42 };
    const b = { id: "b", depends_on: ["a"] as string[], extra: 99 };
    const sorted = topoSortTasks([b, a]);
    expect(sorted[0]).toBe(a);
    expect(sorted[1]).toBe(b);
  });
});

describe("buildTaskRanks", () => {
  it("assigns rank 0 to root node", () => {
    const ranks = buildTaskRanks(["a"], []);
    expect(ranks.get("a")).toBe(0);
  });

  it("assigns rank 1 to single dependent", () => {
    const ranks = buildTaskRanks(["a", "b"], [{ from: "a", to: "b" }]);
    expect(ranks.get("a")).toBe(0);
    expect(ranks.get("b")).toBe(1);
  });

  it("assigns max-predecessor+1 rank for diamond convergence", () => {
    const ranks = buildTaskRanks(
      ["a", "b", "c", "d"],
      [
        { from: "a", to: "b" },
        { from: "a", to: "c" },
        { from: "b", to: "d" },
        { from: "c", to: "d" },
      ],
    );
    expect(ranks.get("a")).toBe(0);
    expect(ranks.get("b")).toBe(1);
    expect(ranks.get("c")).toBe(1);
    expect(ranks.get("d")).toBe(2);
  });

  it("ignores edges referencing unknown node ids", () => {
    const ranks = buildTaskRanks(["a"], [{ from: "x", to: "a" }]);
    expect(ranks.get("a")).toBe(0);
  });

  it("returns an empty map for empty input", () => {
    expect(buildTaskRanks([], [])).toEqual(new Map());
  });
});
