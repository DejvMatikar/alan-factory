import { type CounterCase, type DispatchProposal } from "../../../api";
import EngineBadge from "../../../components/EngineBadge";

export const COUNTER_CASE_ID = "dispatch-counter-case";

export function counterCaseDescribedBy(counterCase: CounterCase | undefined): string | undefined {
  return counterCase ? COUNTER_CASE_ID : undefined;
}

interface CounterCaseSectionProps {
  counterCase: CounterCase | undefined;
}

function CounterCaseSection({ counterCase }: Readonly<CounterCaseSectionProps>) {
  if (!counterCase) return null;
  return (
    <div
      id={COUNTER_CASE_ID}
      data-testid={COUNTER_CASE_ID}
      className="space-y-2 rounded-md border-2 border-amber-500/60 bg-amber-500/10 p-3"
    >
      <p className="text-xs font-semibold uppercase tracking-wide text-amber-300">
        Case against dispatching now
      </p>
      <div className="space-y-1">
        <p className="text-xs font-medium text-text-muted">Strongest argument against</p>
        <p
          data-testid="dispatch-counter-case-against"
          className="whitespace-pre-wrap text-sm text-text-primary"
        >
          {counterCase.against}
        </p>
      </div>
      <div className="space-y-1">
        <p className="text-xs font-medium text-text-muted">Cheapest cut for ~80% of the value</p>
        <p
          data-testid="dispatch-counter-case-cheapest-80"
          className="whitespace-pre-wrap text-sm text-text-primary"
        >
          {counterCase.cheapest_80}
        </p>
      </div>
      <div className="space-y-1">
        <p className="text-xs font-medium text-text-muted">Cost estimate</p>
        <p
          data-testid="dispatch-counter-case-cost-estimate"
          className="whitespace-pre-wrap text-sm text-text-primary"
        >
          {counterCase.cost_estimate}
        </p>
      </div>
    </div>
  );
}

// AC#4 (engine-visibility-and-project-selector): the inherited project/engine
// binding is shown at the dispatch step, but strictly as read-only text — no
// select, button, or click handler that could re-target the already-fixed
// engine this chat's work will land on.
function EngineProvenance({
  engineKey,
}: Readonly<{ engineKey: string | null | undefined }>) {
  if (engineKey == null) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-text-muted">Project engine</p>
      <EngineBadge engineKey={engineKey} testIdSuffix="dispatch-proposal" />
    </div>
  );
}

// Read-only, like EngineProvenance above — this dispatch's roadmap-item
// linkage is a fact carried in from the caller, never an editable field here.
function RoadmapItemProvenance({
  label,
}: Readonly<{ label: string | null | undefined }>) {
  if (label == null) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-text-muted">Roadmap item</p>
      <p data-testid="dispatch-proposal-roadmap-item" className="text-sm text-text-primary">
        {label}
      </p>
    </div>
  );
}

interface ClauseGroupProps {
  heading: string;
  testId: string;
  clauses: string[];
}

function ClauseGroup({ heading, testId, clauses }: Readonly<ClauseGroupProps>) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-text-muted">{heading}</p>
      {clauses.length > 0 && (
        <ul data-testid={testId} className="ml-4 list-disc text-sm text-text-primary">
          {clauses.map((clause, i) => (
            <li key={`${String(i)}-${clause.slice(0, 30)}`}>{clause}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ClauseSection({ proposal }: Readonly<{ proposal: DispatchProposal }>) {
  const blocking = proposal.acceptance_clauses ?? [];
  const recorded = proposal.recorded_clauses ?? [];
  if (blocking.length + recorded.length === 0) return null;
  return (
    <>
      <ClauseGroup
        heading={`Blocking (${String(blocking.length)})`}
        testId="dispatch-proposal-acceptance-clauses"
        clauses={blocking}
      />
      {recorded.length > 0 && (
        <ClauseGroup
          heading={`Recorded (${String(recorded.length)})`}
          testId="dispatch-proposal-recorded-clauses"
          clauses={recorded}
        />
      )}
    </>
  );
}

export interface DispatchProposalFieldsProps {
  proposal: DispatchProposal;
  engineKey?: string | null | undefined;
  roadmapItemLabel?: string | null | undefined;
}

export default function DispatchProposalFields({
  proposal,
  engineKey,
  roadmapItemLabel,
}: Readonly<DispatchProposalFieldsProps>) {
  return (
    <>
      {proposal.title != null && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-text-muted">Title</p>
          <p data-testid="dispatch-proposal-title" className="text-sm text-text-primary">
            {proposal.title}
          </p>
        </div>
      )}
      <EngineProvenance engineKey={engineKey} />
      <RoadmapItemProvenance label={roadmapItemLabel} />
      <div className="space-y-1">
        <p className="text-xs font-medium text-text-muted">Rationale</p>
        <p
          data-testid="dispatch-proposal-rationale"
          className="whitespace-pre-wrap text-sm text-text-primary"
        >
          {proposal.rationale}
        </p>
      </div>
      <ClauseSection proposal={proposal} />
      <CounterCaseSection counterCase={proposal.counter_case} />
    </>
  );
}
