import type { DoDClause, Task } from "../../../api";
import { planningStateLabel } from "../hooks/planningState";
import { deriveTaskTitle } from "../hooks/taskTitle";

type DoDState = "evidenced" | "pending";

const STATE_ICON: Record<DoDState, string> = { evidenced: "✓", pending: "·" };
const STATE_CLASS: Record<DoDState, string> = {
  evidenced: "text-green-400",
  pending: "text-text-muted",
};
const STATE_LABEL: Record<DoDState, string> = {
  evidenced: "delivered",
  pending: "pending",
};

interface TaskDoDPanelProps {
  tasks: readonly Task[];
}

function clauseState(clause: DoDClause): DoDState {
  return clause.evidence == null ? "pending" : "evidenced";
}

function DoDClauseRow({ clause }: Readonly<{ clause: DoDClause }>) {
  const state = clauseState(clause);
  const covers = clause.covers!;
  return (
    <li className="flex items-start gap-2" data-testid={`dod-clause-${clause.id}`}>
      <span
        className={`shrink-0 font-mono uppercase ${STATE_CLASS[state]}`}
        aria-label={`status: ${STATE_LABEL[state]}`}
      >
        {STATE_ICON[state]}
      </span>
      <span className="text-text-primary">
        {clause.text}
        {covers.length > 0 && (
          <span className="ml-1 text-text-muted">(covers {covers.join(", ")})</span>
        )}
        {clause.evidence != null && (
          <span className="ml-1 text-green-400">— {clause.evidence}</span>
        )}
      </span>
    </li>
  );
}

type TaskWithDoD = Task & { dod_clauses: DoDClause[] };

function TaskDoDBlock({ task }: Readonly<{ task: TaskWithDoD }>) {
  return (
    <div className="space-y-1" data-testid={`task-dod-${task.id}`}>
      <p className="text-xs font-medium text-text-primary">
        {deriveTaskTitle(task)}
        {task.planning_state != null && (
          <span className="ml-2 font-mono uppercase text-text-muted">
            {planningStateLabel(task.planning_state)}
          </span>
        )}
        {task.parent_task_id != null && (
          <span className="ml-2 text-text-muted">↳ split from {task.parent_task_id}</span>
        )}
      </p>
      {task.description != null && (
        <p className="text-xs text-text-muted">{task.description}</p>
      )}
      <ul className="space-y-1 list-none">
        {task.dod_clauses.map((clause) => (
          <DoDClauseRow key={clause.id} clause={clause} />
        ))}
      </ul>
    </div>
  );
}

export default function TaskDoDPanel({ tasks }: Readonly<TaskDoDPanelProps>) {
  const withDoD = tasks.filter(
    (t): t is TaskWithDoD => (t.dod_clauses?.length ?? 0) > 0,
  );
  return (
    <div data-testid="task-dod-panel" className="space-y-3 text-xs">
      {withDoD.map((task) => (
        <TaskDoDBlock key={task.id} task={task} />
      ))}
    </div>
  );
}
