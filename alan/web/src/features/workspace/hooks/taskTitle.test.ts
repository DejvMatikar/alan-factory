import { describe, expect, it } from "vitest";

import type { Task } from "../../../api";
import { makeWorkspaceTask } from "../../../test/workspaceFixtures";
import { deriveTaskTitle } from "./taskTitle";

function task(overrides: Partial<Task>): Task {
  return makeWorkspaceTask(overrides);
}

describe("deriveTaskTitle", () => {
  it("returns task.title when set and non-empty", () => {
    expect(deriveTaskTitle(task({ title: "My Task" }))).toBe("My Task");
  });

  it("returns wsTitle for root task with no title when wsTitle provided", () => {
    expect(deriveTaskTitle(task({}), "Workspace Title")).toBe("Workspace Title");
  });

  it("returns task.id for subtask with no title", () => {
    expect(deriveTaskTitle(task({ id: "sub-1", parent_task_id: "root" }))).toBe("sub-1");
  });

  it("formats team label for root task with no title and no wsTitle", () => {
    expect(deriveTaskTitle(task({ team: "implementation_plan" }))).toBe("Implementation Plan");
  });

  it("falls back to task.id when team is null and no title/wsTitle", () => {
    expect(deriveTaskTitle(task({ id: "my-task", team: null }))).toBe("My Task");
  });

  it("ignores wsTitle for subtask with parent", () => {
    expect(deriveTaskTitle(task({ id: "child", parent_task_id: "root" }), "Ignored")).toBe("child");
  });

  it("empty string title does not count as a title", () => {
    expect(deriveTaskTitle(task({ title: "" }), "WS")).toBe("WS");
  });

  it("wsTitle empty string falls through to team label", () => {
    expect(deriveTaskTitle(task({ team: "research" }), "")).toBe("Research");
  });
});
