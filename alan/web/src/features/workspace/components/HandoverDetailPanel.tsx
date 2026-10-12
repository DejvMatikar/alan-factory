import type { JSX } from "react";

import type { Handover } from "../../../api";
import MarkdownPanel from "../../../components/MarkdownPanel";
import { OpenQuestionsSection } from "./OpenQuestionItem";
import {
  ArtifactsSection,
  DecisionsSection,
  TestScenariosSection,
  VerifierMetricsSection,
} from "./handoverSections";

function AssumptionsSection({ assumptions }: Readonly<{ assumptions: string[] }>) {
  if (assumptions.length === 0) return null;
  return (
    <div className="space-y-1">
      <h3 className="text-xs font-medium text-text-primary">Assumptions</h3>
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
        {assumptions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
    </div>
  );
}

function ForbiddenActionsSection({ actions }: Readonly<{ actions: string[] }>) {
  if (actions.length === 0) return null;
  return (
    <div className="space-y-1">
      <h3 className="text-xs font-medium text-text-primary">Forbidden Actions</h3>
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
        {actions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
    </div>
  );
}

function PriorAttemptsSummarySection({
  summary,
}: Readonly<{ summary: string | null | undefined }>) {
  if (summary == null || summary === "") return null;
  return (
    <MarkdownPanel
      title="Prior Attempts Summary"
      ariaLabel="Prior attempts summary"
      testId="prior-attempts-summary"
      defaultOpen={false}
      contentClassName="rounded border border-border bg-surface-elevated p-2 text-xs text-text-primary"
    >
      {summary}
    </MarkdownPanel>
  );
}

function HandoverHeader({ handover }: Readonly<{ handover: Handover }>) {
  const localTime = new Date(handover.timestamp_utc).toLocaleString();
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
      <span className="rounded border border-border bg-surface-elevated px-1.5 py-0.5 font-mono text-text-primary">
        {handover.handover_type}
      </span>
      <span>iter {handover.iteration}</span>
      <time dateTime={handover.timestamp_utc}>{localTime}</time>
    </div>
  );
}

function FullSynthesisSection({ synthesis }: Readonly<{ synthesis: string }>) {
  return (
    <MarkdownPanel
      title="Full Synthesis"
      ariaLabel="Full synthesis"
      testId="full-synthesis"
      defaultOpen={false}
      contentClassName="rounded border border-border bg-surface-elevated p-2 text-xs text-text-primary"
    >
      {synthesis}
    </MarkdownPanel>
  );
}

export default function HandoverDetailPanel({
  handover,
  repo,
}: Readonly<{ handover: Handover; repo: string | null }>): JSX.Element {
  return (
    <div className="max-h-[32rem] space-y-4 overflow-y-auto" data-testid="handover-detail-panel">
      <HandoverHeader handover={handover} />
      <DecisionsSection decisions={handover.decisions!} />
      <VerifierMetricsSection
        completeness={handover.verifier_completeness}
        confidence={handover.verifier_confidence}
        gaps={handover.verifier_gaps}
      />
      <OpenQuestionsSection questions={handover.open_questions!} />
      <AssumptionsSection assumptions={handover.assumptions!} />
      <ArtifactsSection
        artifacts={handover.artifacts_produced!}
        prNumber={handover.pr_number}
        taskId={handover.task_id}
        repo={repo}
      />
      <ForbiddenActionsSection actions={handover.forbidden_actions!} />
      <PriorAttemptsSummarySection summary={handover.prior_attempts_summary} />
      <TestScenariosSection
        scenarios={handover.test_scenarios!}
        results={handover.scenario_results ?? []}
      />
      <FullSynthesisSection synthesis={handover.full_synthesis} />
    </div>
  );
}
