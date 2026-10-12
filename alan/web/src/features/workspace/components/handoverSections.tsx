import type { ArtifactRef, Decision, ScenarioOutcome, TestScenario } from "../../../api";
import GithubRef from "../../../components/GithubRef";
import Disclosure from "../../../ui/Disclosure";

function confidenceBadgeClass(confidence: number): string {
  if (confidence > 0.8) return "rounded px-1.5 py-0.5 text-xs font-medium text-green-400";
  if (confidence >= 0.5) return "rounded px-1.5 py-0.5 text-xs font-medium text-text-primary";
  return "rounded px-1.5 py-0.5 text-xs font-medium text-text-muted";
}

function reversibilityChipClass(r: Decision["reversibility"]): string {
  if (r === "high") return "rounded border border-green-800 px-1.5 py-0.5 text-xs font-medium text-green-400";
  if (r === "medium") return "rounded border border-yellow-800 px-1.5 py-0.5 text-xs font-medium text-yellow-400";
  return "rounded border border-red-800 px-1.5 py-0.5 text-xs font-medium text-red-400";
}

function ConfidenceBadge({ confidence, testId }: Readonly<{ confidence: number; testId: string }>) {
  return (
    <span className={confidenceBadgeClass(confidence)} data-testid={testId}>
      {confidence.toFixed(2)}
    </span>
  );
}

function ReversibilityChip({
  reversibility,
  testId,
}: Readonly<{ reversibility: Decision["reversibility"]; testId: string }>) {
  return (
    <span className={reversibilityChipClass(reversibility)} data-testid={testId}>
      {reversibility}
    </span>
  );
}

function DecisionCard({ decision }: Readonly<{ decision: Decision }>) {
  return (
    <div
      className="space-y-1.5 rounded border border-border bg-surface-elevated p-2"
      data-testid={`handover-decision-${decision.id}`}
    >
      <p className="text-xs font-medium text-text-primary">{decision.statement}</p>
      <p className="text-xs text-text-muted">{decision.rationale}</p>
      <div className="flex items-center gap-2">
        <ConfidenceBadge confidence={decision.confidence} testId={`confidence-${decision.id}`} />
        <ReversibilityChip reversibility={decision.reversibility} testId={`reversibility-${decision.id}`} />
      </div>
    </div>
  );
}

export function DecisionsSection({ decisions }: Readonly<{ decisions: Decision[] }>) {
  if (decisions.length === 0) return null;
  return (
    <div className="space-y-1">
      <h3 className="text-xs font-medium text-text-primary">Decisions</h3>
      <div className="space-y-2">
        {decisions.map((d) => (
          <DecisionCard key={d.id} decision={d} />
        ))}
      </div>
    </div>
  );
}

const SHA_RE = /^[0-9a-f]{7,40}$/i;
const EXTERNAL_URL_RE = /^https?:\/\//;

function localArtifactHref(path: string, taskId: string): string {
  return `/api/workspaces/${taskId}/artifacts?path=${encodeURIComponent(path)}`;
}

function ArtifactLink({
  artifact,
  taskId,
  repo,
}: Readonly<{ artifact: ArtifactRef; taskId: string; repo: string | null }>) {
  const { path, description } = artifact;
  if (SHA_RE.test(path)) {
    return (
      <GithubRef
        repo={repo}
        kind="commit"
        sha={path}
        className="text-xs text-text-muted hover:text-text-primary"
        rel="noopener noreferrer"
        target="_blank"
      >
        {description}
      </GithubRef>
    );
  }
  const isExternal = EXTERNAL_URL_RE.test(path);
  return (
    <a
      href={isExternal ? path : localArtifactHref(path, taskId)}
      className="text-xs text-text-muted hover:text-text-primary"
      target="_blank"
      rel={isExternal ? "noopener noreferrer" : undefined}
    >
      {description}
    </a>
  );
}

export function ArtifactsSection({
  artifacts,
  prNumber,
  taskId,
  repo,
  showHeading = true,
}: Readonly<{
  artifacts: ArtifactRef[];
  prNumber: number | null | undefined;
  taskId: string;
  repo: string | null;
  showHeading?: boolean;
}>) {
  if (artifacts.length === 0) return null;
  return (
    <div className="space-y-1">
      {showHeading && (
        <h3 className="text-xs font-medium text-text-primary">Artifacts Produced</h3>
      )}
      {prNumber != null && (
        <GithubRef
          repo={repo}
          kind="pull"
          prNumber={prNumber}
          className="flex items-center gap-1 text-xs font-medium text-blue-400 hover:text-blue-300"
          rel="noopener noreferrer"
          target="_blank"
          testId="pr-link"
        >
          PR #{prNumber}
        </GithubRef>
      )}
      <ul className="space-y-0.5">
        {artifacts.map((a) => (
          <li key={a.path}>
            <ArtifactLink artifact={a} taskId={taskId} repo={repo} />
          </li>
        ))}
      </ul>
    </div>
  );
}

type ScenarioUiStatus =
  | "passed"
  | "failed"
  | "skipped"
  | "docs_only"
  | "not_run"
  | "advisory"
  | "timeout"
  | "budget_exhausted";

const SCENARIO_STATUS_LABELS: Record<ScenarioUiStatus, string> = {
  passed: "passed",
  failed: "failed",
  skipped: "skipped",
  docs_only: "docs only",
  not_run: "not run",
  advisory: "advisory",
  timeout: "timeout",
  budget_exhausted: "budget exhausted",
};

const SCENARIO_STATUS_STYLES: Record<ScenarioUiStatus, string> = {
  passed: "bg-green-500/15 text-green-300",
  failed: "bg-red-500/15 text-red-300",
  skipped: "bg-amber-500/15 text-amber-300",
  docs_only: "bg-zinc-500/15 text-zinc-300",
  not_run: "bg-slate-500/15 text-slate-300",
  advisory: "bg-sky-500/15 text-sky-300",
  timeout: "bg-red-500/15 text-red-300",
  budget_exhausted: "bg-amber-500/15 text-amber-300",
};

const SCENARIO_OUTCOME_TO_UI_STATUS: Record<ScenarioOutcome["status"], ScenarioUiStatus> = {
  PASS: "passed",
  FAIL: "failed",
  SKIPPED: "skipped",
  DOCUMENTED: "docs_only",
  ADVISORY: "advisory",
  TIMEOUT: "timeout",
  BUDGET_EXHAUSTED: "budget_exhausted",
};

function scenarioUiStatus(outcome: ScenarioOutcome | undefined): ScenarioUiStatus {
  return outcome ? SCENARIO_OUTCOME_TO_UI_STATUS[outcome.status] : "not_run";
}

function WhyDisclosure({
  status,
  diagnostic,
}: Readonly<{ status: ScenarioUiStatus; diagnostic: string }>) {
  return (
    <Disclosure
      preset="row"
      defaultOpen={false}
      className="pl-3 text-[10px] text-text-muted"
      label={`Why ${SCENARIO_STATUS_LABELS[status]}`}
    >
      <p className="pl-3 pt-0.5">{diagnostic}</p>
    </Disclosure>
  );
}

function TestScenarioItem({
  scenario,
  outcome,
}: Readonly<{ scenario: TestScenario; outcome: ScenarioOutcome | undefined }>) {
  const status = scenarioUiStatus(outcome);
  return (
    <li className="space-y-0.5 border-b border-border/60 py-1.5 last:border-0">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-text-muted">{scenario.id}</span>
        <span className="text-xs">{scenario.description}</span>
        <span
          className={`inline-block rounded px-2 py-0.5 text-xs ${SCENARIO_STATUS_STYLES[status]}`}
          data-testid={`scenario-badge-${status}-${scenario.id}`}
        >
          {SCENARIO_STATUS_LABELS[status]}
        </span>
      </div>
      <div className="space-y-0.5 pl-3 text-[10px] text-text-muted">
        <div>
          <span className="font-medium text-text-primary">Given: </span>
          {scenario.given}
        </div>
        <div>
          <span className="font-medium text-text-primary">When: </span>
          {scenario.when}
        </div>
        <div>
          <span className="font-medium text-text-primary">Then: </span>
          {scenario.then}
        </div>
      </div>
      {outcome?.diagnostic && <WhyDisclosure status={status} diagnostic={outcome.diagnostic} />}
    </li>
  );
}

function VerifierMetricsRow({
  completeness,
  confidence,
}: Readonly<{ completeness: number | null; confidence: number | null }>) {
  if (completeness === null && confidence === null) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-text-muted">
      {completeness !== null && (
        <span className="flex items-center gap-1">
          Completeness <ConfidenceBadge confidence={completeness} testId="verifier-completeness" />
        </span>
      )}
      {confidence !== null && (
        <span className="flex items-center gap-1">
          Confidence <ConfidenceBadge confidence={confidence} testId="verifier-confidence" />
        </span>
      )}
    </p>
  );
}

function VerifierGapsList({ gaps }: Readonly<{ gaps: string[] }>) {
  if (gaps.length === 0) return null;
  return (
    <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
      {gaps.map((gap, i) => (
        <li key={`${i}-${gap}`}>{gap}</li>
      ))}
    </ul>
  );
}

export function VerifierMetricsSection({
  completeness,
  confidence,
  gaps,
}: Readonly<{
  completeness: number | null | undefined;
  confidence: number | null | undefined;
  gaps: string[] | undefined;
}>) {
  const c = completeness ?? null;
  const conf = confidence ?? null;
  const gapList = gaps ?? [];
  if (c === null && conf === null && gapList.length === 0) return null;
  return (
    <div className="space-y-1">
      <h3 className="text-xs font-medium text-text-primary">Verifier Metrics</h3>
      <VerifierMetricsRow completeness={c} confidence={conf} />
      <VerifierGapsList gaps={gapList} />
    </div>
  );
}

function OtherScenarioResultItem({ outcome }: Readonly<{ outcome: ScenarioOutcome }>) {
  const status = scenarioUiStatus(outcome);
  return (
    <li className="space-y-0.5 border-b border-border/60 py-1.5 last:border-0">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-text-muted">{outcome.scenario_id}</span>
        <span
          className={`inline-block rounded px-2 py-0.5 text-xs ${SCENARIO_STATUS_STYLES[status]}`}
          data-testid={`scenario-badge-${status}-${outcome.scenario_id}`}
        >
          {SCENARIO_STATUS_LABELS[status]}
        </span>
      </div>
      {outcome.diagnostic && <WhyDisclosure status={status} diagnostic={outcome.diagnostic} />}
    </li>
  );
}

export function TestScenariosSection({
  scenarios,
  results,
}: Readonly<{ scenarios: TestScenario[]; results: ScenarioOutcome[] }>) {
  if (scenarios.length === 0) return null;
  const scenarioIds = new Set(scenarios.map((s) => s.id));
  const outcomeById = new Map(results.map((r) => [r.scenario_id, r]));
  const unmatchedResults = results.filter((r) => !scenarioIds.has(r.scenario_id));
  return (
    <div className="space-y-1">
      <h3 className="text-xs font-medium text-text-primary">Test Scenarios</h3>
      <ul>
        {scenarios.map((s) => (
          <TestScenarioItem key={s.id} scenario={s} outcome={outcomeById.get(s.id)} />
        ))}
      </ul>
      {unmatchedResults.length > 0 && (
        <div className="space-y-1 pt-1">
          <h4 className="text-xs font-medium text-text-primary">Other scenario results</h4>
          <ul>
            {unmatchedResults.map((r) => (
              <OtherScenarioResultItem key={r.scenario_id} outcome={r} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
