import { deriveAssumptions, type Assumption, type Requirement, type Task, type WorkspaceStatus } from "../../../api";
import Disclosure from "../../../ui/Disclosure";
import AssumptionsList from "./AssumptionsList";
import RequirementsChecklist, { coverageFor, requirementDisplay, type Coverage } from "./RequirementsChecklist";
import TaskDoDPanel from "./TaskDoDPanel";

const FINISHED_STATUSES: ReadonlySet<WorkspaceStatus> = new Set([
  "completed",
  "escalated",
  "failed",
  "partial",
  "archived",
]);

interface AcceptanceChecklistProps {
  requirements: Requirement[] | null | undefined;
  assumptions: string[] | null | undefined;
  tasks: readonly Task[];
  unprovenClauseIds: ReadonlySet<string>;
  workspaceStatus: WorkspaceStatus;
}

interface ChecklistStats {
  readonly reqs: Requirement[];
  readonly total: number;
  readonly met: number;
  readonly unprovenCount: number;
  readonly dodTotal: number;
  readonly derivedAssumptions: Assumption[] | null;
}

function totalDoDClauses(tasks: readonly Task[]): number {
  return tasks.reduce((sum, t) => sum + (t.dod_clauses?.length ?? 0), 0);
}

function metDoDClauses(tasks: readonly Task[]): number {
  return tasks.reduce(
    (sum, t) => sum + (t.dod_clauses?.filter((c) => c.evidence != null).length ?? 0),
    0,
  );
}

function isMetDisplay(
  display: ReturnType<typeof requirementDisplay>,
  req: Requirement,
  coverage: Coverage | null,
): boolean {
  if (display === "PASS" || display === "WITHDRAWN" || display === "WITHDRAWN_BY_OPERATOR") {
    return true;
  }
  return req.test_ref == null && coverage?.clause.evidence != null;
}

function computeStats(
  requirements: Requirement[] | null | undefined,
  tasks: readonly Task[],
  assumptions: string[] | null | undefined,
  unprovenClauseIds: ReadonlySet<string>,
): ChecklistStats {
  const reqs = requirements ?? [];
  const dodTotal = totalDoDClauses(tasks);
  const derivedAssumptions = deriveAssumptions(assumptions);
  const total = reqs.length + dodTotal;
  let reqsMet = 0;
  let unprovenCount = 0;
  for (const req of reqs) {
    const display = requirementDisplay(req, unprovenClauseIds);
    if (isMetDisplay(display, req, coverageFor(req.id, tasks))) reqsMet++;
    if (display === "UNPROVEN") unprovenCount++;
  }
  const met = reqsMet + metDoDClauses(tasks);
  return { reqs, total, met, unprovenCount, dodTotal, derivedAssumptions };
}

function hasNothingToCheck(stats: ChecklistStats): boolean {
  return stats.total === 0 && stats.derivedAssumptions === null;
}

function collapsesByDefault(stats: ChecklistStats, workspaceStatus: WorkspaceStatus): boolean {
  const finished = FINISHED_STATUSES.has(workspaceStatus);
  const allMet = stats.total > 0 && stats.met === stats.total;
  return finished && allMet;
}

function summaryText(met: number, total: number, unproven: number): string {
  const base = `${String(met)} of ${String(total)} met`;
  return unproven > 0 ? `${base} (${String(unproven)} never mechanically checked)` : base;
}

export default function AcceptanceChecklist({
  requirements,
  assumptions,
  tasks,
  unprovenClauseIds,
  workspaceStatus,
}: Readonly<AcceptanceChecklistProps>) {
  const stats = computeStats(requirements, tasks, assumptions, unprovenClauseIds);
  if (hasNothingToCheck(stats)) return null;

  return (
    <section data-testid="acceptance-checklist" className="space-y-2">
      <Disclosure
        preset="panel"
        defaultOpen={!collapsesByDefault(stats, workspaceStatus)}
        testId="acceptance-checklist-disclosure"
        label={
          <span data-testid="acceptance-checklist-summary">
            {summaryText(stats.met, stats.total, stats.unprovenCount)}
          </span>
        }
      >
        <div className="mt-2 space-y-4">
          {stats.reqs.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Requirements</h3>
              <RequirementsChecklist
                requirements={stats.reqs}
                unprovenClauseIds={unprovenClauseIds}
                tasks={tasks}
              />
            </div>
          )}
          {stats.dodTotal > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Definition of done</h3>
              <TaskDoDPanel tasks={tasks} />
            </div>
          )}
          {stats.derivedAssumptions !== null && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Assumptions</h3>
              <AssumptionsList assumptions={stats.derivedAssumptions} />
            </div>
          )}
        </div>
      </Disclosure>
    </section>
  );
}
