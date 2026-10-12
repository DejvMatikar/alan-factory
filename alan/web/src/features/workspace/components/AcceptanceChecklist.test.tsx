import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Requirement, Task } from "../../../api";
import { expectDisclosureState } from "../../../test/disclosure";
import { makeWorkspaceTask } from "../../../test/workspaceFixtures";
import AcceptanceChecklist from "./AcceptanceChecklist";

const EMPTY_SET: ReadonlySet<string> = new Set();
const NO_TASKS: Task[] = [];

function passingReq(id: string): Requirement {
  return {
    id,
    text: `req ${id}`,
    test_ref: `test_${id}`,
    status: "PASS",
    merged_pr_url: null,
    withdrawn_by_question_id: null,
  };
}

function pendingReq(id: string): Requirement {
  return { ...passingReq(id), test_ref: null, status: "PENDING" };
}

describe("AcceptanceChecklist", () => {
  it("renders nothing when there are no requirements, DoD clauses, or assumptions", () => {
    const { container } = render(
      <AcceptanceChecklist
        requirements={null}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for an empty requirements array with no other checkable data", () => {
    const { container } = render(
      <AcceptanceChecklist
        requirements={[]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the container when only assumptions are present", () => {
    render(
      <AcceptanceChecklist
        requirements={null}
        assumptions={["Assumed retries are safe"]}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist")).toBeInTheDocument();
    expect(screen.getByTestId("assumptions-checklist")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Requirements" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Definition of done" })).toBeNull();
  });

  it("renders a Requirements sub-section only when requirements are present", () => {
    render(
      <AcceptanceChecklist
        requirements={[passingReq("a")]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByRole("heading", { level: 3, name: "Requirements" })).toBeInTheDocument();
    expect(screen.getByTestId("requirements-checklist")).toBeInTheDocument();
  });

  it("renders a Definition of done sub-section only when a task has DoD clauses", () => {
    const task = makeWorkspaceTask({
      id: "be",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: [], evidence: "PR #5" }],
    });
    render(
      <AcceptanceChecklist
        requirements={null}
        assumptions={null}
        tasks={[task]}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByRole("heading", { level: 3, name: "Definition of done" })).toBeInTheDocument();
    expect(screen.getByTestId("task-dod-panel")).toBeInTheDocument();
    expect(screen.getByTestId("task-dod-be")).toBeInTheDocument();
  });

  it("shows an 'N of M met' summary counting requirements and DoD clauses together", () => {
    const task = makeWorkspaceTask({
      id: "be",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: [], evidence: "PR #5" }],
    });
    render(
      <AcceptanceChecklist
        requirements={[passingReq("a"), pendingReq("b")]}
        assumptions={null}
        tasks={[task]}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).toHaveTextContent("2 of 3 met");
  });

  it("counts an unbound requirement as met when a covering DoD clause has evidence", () => {
    const task = makeWorkspaceTask({
      id: "be",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: ["a"], evidence: "PR #7" }],
    });
    render(
      <AcceptanceChecklist
        requirements={[pendingReq("a")]}
        assumptions={null}
        tasks={[task]}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).toHaveTextContent("2 of 2 met");
  });

  it("does not count a covered-but-unevidenced requirement as met", () => {
    const task = makeWorkspaceTask({
      id: "be",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: ["a"], evidence: null }],
    });
    render(
      <AcceptanceChecklist
        requirements={[pendingReq("a")]}
        assumptions={null}
        tasks={[task]}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).toHaveTextContent("0 of 2 met");
  });

  it("does not apply the covered-and-evidenced rule to a test-bound requirement whose test hasn't passed", () => {
    const task = makeWorkspaceTask({
      id: "be",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: ["a"], evidence: "PR #7" }],
    });
    const boundPending: Requirement = { ...pendingReq("a"), test_ref: "test_a" };
    render(
      <AcceptanceChecklist
        requirements={[boundPending]}
        assumptions={null}
        tasks={[task]}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).toHaveTextContent("1 of 2 met");
  });

  it("collapses by default when finished and a covered-and-evidenced requirement makes everything met", async () => {
    const task = makeWorkspaceTask({
      id: "be",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: ["a"], evidence: "PR #7" }],
    });
    render(
      <AcceptanceChecklist
        requirements={[pendingReq("a")]}
        assumptions={null}
        tasks={[task]}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="completed"
      />,
    );
    await expectDisclosureState(screen.getByTestId("acceptance-checklist-disclosure"), false);
  });

  it("does not count assumptions towards the 'N of M met' summary", () => {
    render(
      <AcceptanceChecklist
        requirements={[passingReq("a")]}
        assumptions={["an assumption"]}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).toHaveTextContent("1 of 1 met");
  });

  it("includes the never-mechanically-checked count in the summary when present", () => {
    render(
      <AcceptanceChecklist
        requirements={[pendingReq("a")]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={new Set(["a"])}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).toHaveTextContent(
      "0 of 1 met (1 never mechanically checked)",
    );
  });

  it("omits the never-mechanically-checked count when there is nothing unproven", () => {
    render(
      <AcceptanceChecklist
        requirements={[passingReq("a")]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).not.toHaveTextContent(
      "never mechanically checked",
    );
  });

  it("counts a withdrawn requirement as met", () => {
    const withdrawn: Requirement = { ...pendingReq("a"), withdrawn_by_question_id: "q1" };
    render(
      <AcceptanceChecklist
        requirements={[withdrawn]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).toHaveTextContent("1 of 1 met");
  });

  it("counts an operator-withdrawn requirement as met", () => {
    const withdrawn: Requirement = {
      ...pendingReq("a"),
      withdrawn_by_question_id: "operator:withdrawal",
    };
    render(
      <AcceptanceChecklist
        requirements={[withdrawn]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    expect(screen.getByTestId("acceptance-checklist-summary")).toHaveTextContent("1 of 1 met");
  });

  it("expands by default when the workspace is still running", async () => {
    render(
      <AcceptanceChecklist
        requirements={[passingReq("a")]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="active"
      />,
    );
    await expectDisclosureState(screen.getByTestId("acceptance-checklist-disclosure"), true);
  });

  it("expands by default when finished but not everything passed", async () => {
    render(
      <AcceptanceChecklist
        requirements={[pendingReq("a")]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="completed"
      />,
    );
    await expectDisclosureState(screen.getByTestId("acceptance-checklist-disclosure"), true);
  });

  it("collapses by default when the workspace is finished and everything passed", async () => {
    render(
      <AcceptanceChecklist
        requirements={[passingReq("a")]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="completed"
      />,
    );
    await expectDisclosureState(screen.getByTestId("acceptance-checklist-disclosure"), false);
  });

  it("stays expanded on a paused workspace even when everything currently passes", async () => {
    render(
      <AcceptanceChecklist
        requirements={[passingReq("a")]}
        assumptions={null}
        tasks={NO_TASKS}
        unprovenClauseIds={EMPTY_SET}
        workspaceStatus="paused"
      />,
    );
    await expectDisclosureState(screen.getByTestId("acceptance-checklist-disclosure"), true);
  });
});
