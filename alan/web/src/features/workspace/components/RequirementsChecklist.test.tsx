import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DoDClause, Requirement, Task } from "../../../api";
import { makeWorkspaceTask } from "../../../test/workspaceFixtures";
import RequirementsChecklist from "./RequirementsChecklist";

const PASS_REQ: Requirement = {
  id: "req-pass",
  text: "The API returns 200",
  test_ref: "test_api_200",
  status: "PASS",
  merged_pr_url: null,
  withdrawn_by_question_id: null,
};

const FAIL_REQ: Requirement = {
  id: "req-fail",
  text: "Error handling works",
  test_ref: "test_error",
  status: "FAIL",
  merged_pr_url: null,
  withdrawn_by_question_id: null,
};

const PENDING_REQ: Requirement = {
  id: "req-pending",
  text: "Auth succeeds",
  test_ref: null,
  status: "PENDING",
  merged_pr_url: null,
  withdrawn_by_question_id: null,
};

const WITHDRAWN_REQ: Requirement = {
  id: "req-withdrawn",
  text: "Legacy endpoint stays disabled",
  test_ref: "test_legacy_disabled",
  status: "FAIL",
  merged_pr_url: null,
  withdrawn_by_question_id: "clause-stall-req-withdrawn",
};

const OPERATOR_WITHDRAWN_REQ: Requirement = {
  id: "req-operator-withdrawn",
  text: "Legacy endpoint stays disabled",
  test_ref: "test_legacy_disabled",
  status: "FAIL",
  merged_pr_url: null,
  withdrawn_by_question_id: "operator:withdrawal",
};

const RECORD_REQ: Requirement = {
  id: "req-record",
  text: "The docs say X",
  test_ref: null,
  status: "PENDING",
  merged_pr_url: null,
  withdrawn_by_question_id: null,
  kind: "record",
};

const EMPTY_SET: ReadonlySet<string> = new Set();
const NO_TASKS: Task[] = [];

describe("RequirementsChecklist", () => {
  it("renders the list container with data-testid", () => {
    render(
      <RequirementsChecklist requirements={[PASS_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.getByTestId("requirements-checklist")).toBeInTheDocument();
  });

  it("renders a row for each requirement", () => {
    render(
      <RequirementsChecklist
        requirements={[PASS_REQ, FAIL_REQ, PENDING_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={NO_TASKS}
      />,
    );
    expect(screen.getByTestId("requirement-req-pass")).toBeInTheDocument();
    expect(screen.getByTestId("requirement-req-fail")).toBeInTheDocument();
    expect(screen.getByTestId("requirement-req-pending")).toBeInTheDocument();
  });

  it("renders an unbound clause with a null test_ref", () => {
    const unbound: Requirement = {
      id: "req-unbound",
      text: "Clause with no bound test",
      test_ref: null,
      status: "PENDING",
      merged_pr_url: null,
      withdrawn_by_question_id: null,
    };
    render(
      <RequirementsChecklist requirements={[unbound]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.getByTestId("requirement-req-unbound")).toBeInTheDocument();
    expect(screen.getByText("Clause with no bound test")).toBeInTheDocument();
  });

  it("shows requirement text", () => {
    render(
      <RequirementsChecklist requirements={[PASS_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.getByText("The API returns 200")).toBeInTheDocument();
  });

  it("shows checkmark for PASS", () => {
    render(
      <RequirementsChecklist requirements={[PASS_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.getByLabelText("status: pass")).toHaveTextContent("✓");
  });

  it("shows cross for FAIL", () => {
    render(
      <RequirementsChecklist requirements={[FAIL_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.getByLabelText("status: fail")).toHaveTextContent("✗");
  });

  it("shows dot for PENDING", () => {
    render(
      <RequirementsChecklist requirements={[PENDING_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.getByLabelText("status: pending")).toHaveTextContent("·");
  });

  it("renders empty list without error", () => {
    render(<RequirementsChecklist requirements={[]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />);
    expect(screen.getByTestId("requirements-checklist")).toBeInTheDocument();
    expect(screen.queryByTestId(/^requirement-/)).toBeNull();
  });

  it("renders the unproven marker instead of the status marker for a clause id in the unproven set", () => {
    render(
      <RequirementsChecklist
        requirements={[PENDING_REQ]}
        unprovenClauseIds={new Set(["req-pending"])}
        tasks={NO_TASKS}
      />,
    );
    expect(screen.queryByLabelText("status: pending")).not.toBeInTheDocument();
    const marker = screen.getByLabelText("status: not mechanically checked");
    expect(marker).toHaveTextContent("⊘");
    expect(marker.className).toContain("text-status-warning-fg");
  });

  it("prioritizes the unproven marker over a PASS status when the clause id is unproven", () => {
    render(
      <RequirementsChecklist
        requirements={[PASS_REQ]}
        unprovenClauseIds={new Set(["req-pass"])}
        tasks={NO_TASKS}
      />,
    );
    expect(screen.queryByLabelText("status: pass")).not.toBeInTheDocument();
    expect(screen.getByLabelText("status: not mechanically checked")).toBeInTheDocument();
  });

  it("shows the withdrawn marker instead of the status marker for a withdrawn clause", () => {
    render(
      <RequirementsChecklist requirements={[WITHDRAWN_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.queryByLabelText("status: fail")).not.toBeInTheDocument();
    const marker = screen.getByLabelText("status: withdrawn");
    expect(marker).toHaveTextContent("⊗");
    expect(marker.className).toContain("text-status-neutral-fg");
  });

  it("prioritizes the withdrawn marker over the unproven marker when both would apply", () => {
    render(
      <RequirementsChecklist
        requirements={[WITHDRAWN_REQ]}
        unprovenClauseIds={new Set(["req-withdrawn"])}
        tasks={NO_TASKS}
      />,
    );
    expect(screen.queryByLabelText("status: not mechanically checked")).not.toBeInTheDocument();
    expect(screen.getByLabelText("status: withdrawn")).toBeInTheDocument();
  });

  it("shows which question withdrew the clause", () => {
    render(
      <RequirementsChecklist requirements={[WITHDRAWN_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.getByTestId("requirement-req-withdrawn-withdrawn-by")).toHaveTextContent(
      "clause-stall-req-withdrawn",
    );
  });

  it("shows the withdrawn-by-operator marker for a clause withdrawn by the operator sentinel", () => {
    render(
      <RequirementsChecklist
        requirements={[OPERATOR_WITHDRAWN_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={NO_TASKS}
      />,
    );
    expect(screen.queryByLabelText("status: withdrawn")).not.toBeInTheDocument();
    const marker = screen.getByLabelText("status: withdrawn by operator");
    expect(marker).toHaveTextContent("⊗");
    expect(marker.className).toContain("text-status-neutral-fg");
  });

  it("renders the operator withdrawal sub-span text distinctly from a question withdrawal", () => {
    render(
      <RequirementsChecklist
        requirements={[OPERATOR_WITHDRAWN_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={NO_TASKS}
      />,
    );
    expect(screen.getByTestId("requirement-req-operator-withdrawn-withdrawn-by")).toHaveTextContent(
      "(withdrawn by operator)",
    );
  });

  it("marks a record clause with a neutral chip carrying the human label", () => {
    render(
      <RequirementsChecklist requirements={[RECORD_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    const chip = screen.getByTestId("requirement-req-record-kind");
    expect(chip).toHaveTextContent("prose evidence");
    expect(chip).not.toHaveTextContent("record");
    expect(chip.className).toContain("text-status-neutral-fg");
    expect(chip.className).toContain("bg-status-neutral-tint/15");
  });

  it("gives the record chip an accessible name distinct from the status marker's", () => {
    render(
      <RequirementsChecklist requirements={[RECORD_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    const chip = screen.getByLabelText("evidence: prose evidence");
    const status = screen.getByLabelText("status: pending");
    expect(chip).not.toBe(status);
    expect(chip.getAttribute("aria-label")).not.toBe(status.getAttribute("aria-label"));
  });

  it("leaves a clause with no kind unmarked", () => {
    render(
      <RequirementsChecklist requirements={[PENDING_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.queryByTestId("requirement-req-pending-kind")).toBeNull();
  });

  it("leaves a behaviour clause unmarked", () => {
    render(
      <RequirementsChecklist
        requirements={[{ ...PENDING_REQ, kind: "behaviour" }]}
        unprovenClauseIds={EMPTY_SET}
        tasks={NO_TASKS}
      />,
    );
    expect(screen.queryByTestId("requirement-req-pending-kind")).toBeNull();
  });

  it("renders the status marker, the withdrawn note and the chip together in that order", () => {
    render(
      <RequirementsChecklist
        requirements={[{ ...RECORD_REQ, withdrawn_by_question_id: "clause-stall-req-record" }]}
        unprovenClauseIds={EMPTY_SET}
        tasks={NO_TASKS}
      />,
    );
    const status = screen.getByLabelText("status: withdrawn");
    const withdrawnBy = screen.getByTestId("requirement-req-record-withdrawn-by");
    const chip = screen.getByTestId("requirement-req-record-kind");
    expect(status.nextElementSibling).toBe(withdrawnBy);
    expect(withdrawnBy.nextElementSibling).toBe(chip);
  });

  it("shows evidence from a bound test_ref", () => {
    render(
      <RequirementsChecklist requirements={[PASS_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.getByTestId("requirement-req-pass-evidence")).toHaveTextContent(
      "evidence: test_api_200",
    );
  });

  it("omits evidence when there is no test_ref and no covering DoD clause with evidence", () => {
    render(
      <RequirementsChecklist requirements={[PENDING_REQ]} unprovenClauseIds={EMPTY_SET} tasks={NO_TASKS} />,
    );
    expect(screen.queryByTestId("requirement-req-pending-evidence")).toBeNull();
  });

  it("falls back to a covering DoD clause's evidence when the requirement has no test_ref", () => {
    const task = makeWorkspaceTask({
      id: "be",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: ["req-pending"], evidence: "PR #7" }],
    });
    render(
      <RequirementsChecklist
        requirements={[PENDING_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={[task]}
      />,
    );
    expect(screen.getByTestId("requirement-req-pending-evidence")).toHaveTextContent("evidence: PR #7");
  });

  it("shows the task that covers a requirement via a DoD clause's covers list", () => {
    const task = makeWorkspaceTask({
      id: "be",
      title: "Backend",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: ["req-pending"], evidence: null }],
    });
    render(
      <RequirementsChecklist
        requirements={[PENDING_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={[task]}
      />,
    );
    expect(screen.getByTestId("requirement-req-pending-covered-by")).toHaveTextContent(
      "covered by Backend",
    );
  });

  it("skips a task with no dod_clauses field when looking for coverage", () => {
    const bareTask = makeWorkspaceTask({ id: "bare" });
    render(
      <RequirementsChecklist
        requirements={[PENDING_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={[bareTask]}
      />,
    );
    expect(screen.queryByTestId("requirement-req-pending-covered-by")).toBeNull();
  });

  it("skips a DoD clause with no covers field when looking for coverage", () => {
    const bareClause = { id: "dod-1", text: "endpoint works", evidence: null } as DoDClause;
    const task = makeWorkspaceTask({ id: "be", dod_clauses: [bareClause] });
    render(
      <RequirementsChecklist
        requirements={[PENDING_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={[task]}
      />,
    );
    expect(screen.queryByTestId("requirement-req-pending-covered-by")).toBeNull();
  });

  it("prefers a covering clause with evidence over an earlier one without, for both evidence and the covering task label", () => {
    const taskNoEvidence = makeWorkspaceTask({
      id: "be1",
      title: "Backend prep",
      dod_clauses: [{ id: "dod-1", text: "prep", covers: ["req-pending"], evidence: null }],
    });
    const taskWithEvidence = makeWorkspaceTask({
      id: "be2",
      title: "Backend ship",
      dod_clauses: [{ id: "dod-2", text: "ship", covers: ["req-pending"], evidence: "PR #9" }],
    });
    render(
      <RequirementsChecklist
        requirements={[PENDING_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={[taskNoEvidence, taskWithEvidence]}
      />,
    );
    expect(screen.getByTestId("requirement-req-pending-evidence")).toHaveTextContent(
      "evidence: PR #9",
    );
    expect(screen.getByTestId("requirement-req-pending-covered-by")).toHaveTextContent(
      "covered by Backend ship",
    );
  });

  it("falls back to the first covering clause's task label when no covering clause has evidence", () => {
    const taskA = makeWorkspaceTask({
      id: "be1",
      title: "Backend prep",
      dod_clauses: [{ id: "dod-1", text: "prep", covers: ["req-pending"], evidence: null }],
    });
    const taskB = makeWorkspaceTask({
      id: "be2",
      title: "Backend ship",
      dod_clauses: [{ id: "dod-2", text: "ship", covers: ["req-pending"], evidence: null }],
    });
    render(
      <RequirementsChecklist
        requirements={[PENDING_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={[taskA, taskB]}
      />,
    );
    expect(screen.queryByTestId("requirement-req-pending-evidence")).toBeNull();
    expect(screen.getByTestId("requirement-req-pending-covered-by")).toHaveTextContent(
      "covered by Backend prep",
    );
  });

  it("omits the covering-task note when no DoD clause covers the requirement", () => {
    const task = makeWorkspaceTask({
      id: "be",
      dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: ["some-other-req"], evidence: null }],
    });
    render(
      <RequirementsChecklist
        requirements={[PENDING_REQ]}
        unprovenClauseIds={EMPTY_SET}
        tasks={[task]}
      />,
    );
    expect(screen.queryByTestId("requirement-req-pending-covered-by")).toBeNull();
  });
});
