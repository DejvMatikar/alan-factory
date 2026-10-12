import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Task } from "../../../api";
import { makeWorkspaceTask } from "../../../test/workspaceFixtures";
import TaskDoDPanel from "./TaskDoDPanel";

function task(overrides: Partial<Task>): Task {
  return makeWorkspaceTask({ team: "implementation", status: "completed", ...overrides });
}

describe("TaskDoDPanel", () => {
  it("omits a task with no DoD clauses", () => {
    render(<TaskDoDPanel tasks={[task({ id: "bare" })]} />);
    expect(screen.getByTestId("task-dod-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("task-dod-bare")).toBeNull();
  });

  it("renders an evidenced clause with covers, evidence, planning state, lineage, and description", () => {
    render(
      <TaskDoDPanel
        tasks={[
          task({
            id: "be",
            planning_state: "detailed",
            description: "build the endpoint",
            parent_task_id: "root",
            dod_clauses: [
              {
                id: "dod-1",
                text: "endpoint returns 200",
                covers: ["req-a", "req-b"],
                evidence: "PR #5",
              },
            ],
          }),
        ]}
      />,
    );
    const li = screen.getByTestId("dod-clause-dod-1");
    expect(screen.getByTestId("task-dod-be")).toBeInTheDocument();
    expect(screen.getByLabelText("status: delivered")).toHaveTextContent("✓");
    expect(li).toHaveTextContent("endpoint returns 200");
    expect(li).toHaveTextContent("covers req-a, req-b");
    expect(li).toHaveTextContent("PR #5");
    expect(screen.getByText("Detailed plan")).toBeInTheDocument();
    expect(screen.getByText(/split from root/)).toBeInTheDocument();
    expect(screen.getByText("build the endpoint")).toBeInTheDocument();
  });

  it("renders a pending clause without covers, evidence, lineage, or description", () => {
    render(
      <TaskDoDPanel
        tasks={[
          task({
            id: "fe",
            team: null,
            dod_clauses: [{ id: "dod-2", text: "renders", covers: [], evidence: null }],
          }),
        ]}
      />,
    );
    expect(screen.getByTestId("task-dod-fe")).toHaveTextContent("Fe");
    expect(screen.getByLabelText("status: pending")).toHaveTextContent("·");
    expect(screen.getByTestId("dod-clause-dod-2")).toHaveTextContent("renders");
    expect(screen.queryByText(/covers/)).toBeNull();
    expect(screen.queryByText(/split from/)).toBeNull();
  });
});
