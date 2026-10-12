import type { DoDClause, Requirement, RequirementStatus, Task } from "../../../api";
import { deriveTaskTitle } from "../hooks/taskTitle";
import ClauseList from "./ClauseList";

interface RequirementsChecklistProps {
  requirements: Requirement[];
  unprovenClauseIds: ReadonlySet<string>;
  tasks: readonly Task[];
}

type ClauseDisplay = "WITHDRAWN" | "WITHDRAWN_BY_OPERATOR" | "UNPROVEN" | RequirementStatus;

const OPERATOR_WITHDRAWAL_SENTINEL = "operator:withdrawal";

const STATUS_LABELS: Record<ClauseDisplay, string> = {
  WITHDRAWN: "withdrawn",
  WITHDRAWN_BY_OPERATOR: "withdrawn by operator",
  UNPROVEN: "not mechanically checked",
  PASS: "pass",
  FAIL: "fail",
  PENDING: "pending",
};

const KIND_LABELS: Record<"record", string> = {
  record: "prose evidence",
};

const STATUS_CLASSES: Record<ClauseDisplay, string> = {
  WITHDRAWN: "text-status-neutral-fg",
  WITHDRAWN_BY_OPERATOR: "text-status-neutral-fg",
  UNPROVEN: "text-status-warning-fg",
  PASS: "text-green-400",
  FAIL: "text-red-400",
  PENDING: "text-text-muted",
};

const STATUS_ICONS: Record<ClauseDisplay, string> = {
  WITHDRAWN: "⊗",
  WITHDRAWN_BY_OPERATOR: "⊗",
  UNPROVEN: "⊘",
  PASS: "✓",
  FAIL: "✗",
  PENDING: "·",
};

export function requirementDisplay(
  req: Requirement,
  unprovenClauseIds: ReadonlySet<string>,
): ClauseDisplay {
  if (req.withdrawn_by_question_id === OPERATOR_WITHDRAWAL_SENTINEL) return "WITHDRAWN_BY_OPERATOR";
  if (req.withdrawn_by_question_id !== null) return "WITHDRAWN";
  if (unprovenClauseIds.has(req.id)) return "UNPROVEN";
  return req.status;
}

export interface Coverage {
  readonly task: Task;
  readonly clause: DoDClause;
}

function coverageInTask(task: Task, reqId: string): Coverage | null {
  let first: Coverage | null = null;
  for (const clause of task.dod_clauses ?? []) {
    if (!(clause.covers ?? []).includes(reqId)) continue;
    const candidate: Coverage = { task, clause };
    if (clause.evidence != null) return candidate;
    first ??= candidate;
  }
  return first;
}

export function coverageFor(reqId: string, tasks: readonly Task[]): Coverage | null {
  let first: Coverage | null = null;
  for (const task of tasks) {
    const found = coverageInTask(task, reqId);
    if (found?.clause.evidence != null) return found;
    first ??= found;
  }
  return first;
}

function evidenceFor(req: Requirement, coverage: Coverage | null): string | null {
  return req.test_ref ?? coverage?.clause.evidence ?? null;
}

function requirementMarker(req: Requirement, unprovenClauseIds: ReadonlySet<string>, tasks: readonly Task[]) {
  const display = requirementDisplay(req, unprovenClauseIds);
  const coverage = coverageFor(req.id, tasks);
  const evidence = evidenceFor(req, coverage);
  return (
    <>
      <span
        className={`shrink-0 font-mono uppercase ${STATUS_CLASSES[display]}`}
        aria-label={`status: ${STATUS_LABELS[display]}`}
      >
        {STATUS_ICONS[display]}
      </span>
      {req.withdrawn_by_question_id !== null && (
        <span
          data-testid={`requirement-${req.id}-withdrawn-by`}
          className="shrink-0 text-text-muted"
        >
          {display === "WITHDRAWN_BY_OPERATOR"
            ? "(withdrawn by operator)"
            : `(withdrawn by ${req.withdrawn_by_question_id})`}
        </span>
      )}
      {req.kind === "record" && (
        <span
          data-testid={`requirement-${req.id}-kind`}
          className="shrink-0 inline-block rounded px-2 py-0.5 text-xs bg-status-neutral-tint/15 text-status-neutral-fg"
          aria-label={`evidence: ${KIND_LABELS.record}`}
        >
          {KIND_LABELS.record}
        </span>
      )}
      {evidence != null && (
        <span
          data-testid={`requirement-${req.id}-evidence`}
          className="shrink-0 text-text-muted"
        >
          evidence: {evidence}
        </span>
      )}
      {coverage !== null && (
        <span
          data-testid={`requirement-${req.id}-covered-by`}
          className="shrink-0 text-text-muted"
        >
          covered by {deriveTaskTitle(coverage.task)}
        </span>
      )}
    </>
  );
}

export default function RequirementsChecklist({
  requirements,
  unprovenClauseIds,
  tasks,
}: Readonly<RequirementsChecklistProps>) {
  return (
    <ClauseList
      items={requirements}
      testId="requirements-checklist"
      itemTestId={(req) => `requirement-${req.id}`}
      renderMarker={(req) => requirementMarker(req, unprovenClauseIds, tasks)}
    />
  );
}
