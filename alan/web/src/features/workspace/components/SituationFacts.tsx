import { useEffect, useState } from "react";

import {
  api,
  type Blocker,
  type Handover,
  type ManagerSummary,
  type OpenQuestion,
  type ParkReason,
  type PendingDeployGate,
  type TerminalOutcome,
  type UnblockRecord,
  type WorkspaceDelivery,
} from "../../../api";
import { useDocQuery } from "../../../api/queries";
import Button from "../../../components/Button";
import { errorMessageOf } from "../../../lib/errorMessage";
import { formatActiveDuration, formatDate, formatRelativeTime } from "../../../lib/format";
import { pullUrl } from "../../../lib/githubLinks";
import {
  authorityHolderLabel,
  isHostOwnedOutcome,
  SILENCE_THRESHOLD_MINUTES,
  SILENCE_THRESHOLD_MS,
  unblockView,
  type UnblockDisplayState,
  type UnblockView,
} from "../../../lib/unblockView";
import { formatMergeBlockLabel } from "../../../mergeBlockLabel";
import { useAction } from "../../../ui/useAction";
import type { Situation, SituationDecision } from "../hooks/situation";
import type { Severity, WorkspaceNotice } from "../hooks/workspaceNotices";
import ActivityFeed from "./ActivityFeed";
import { OpenQuestionsSection } from "./OpenQuestionItem";

const STUCK_SEVERITY_STYLES: Record<Severity, string> = {
  warn: "border-amber-500/50 bg-amber-500/15 text-amber-300",
  error: "border-red-500/50 bg-red-500/15 text-red-300",
  info: "border-border bg-surface-elevated text-text-muted",
};

function StickyNoticeFact({ notice }: Readonly<{ notice: WorkspaceNotice | null }>) {
  if (notice === null) return null;
  return (
    <div
      data-testid="stuck-banner"
      role="alert"
      className={`rounded-md border px-3 py-2 text-xs ${STUCK_SEVERITY_STYLES[notice.severity]}`}
    >
      {notice.text}
    </div>
  );
}

const PARK_REASON_LABELS: Record<ParkReason, string> = {
  question: "Question for you",
  crash: "Worker crashed",
  merge_block: "Merge blocked",
  stall: "Dependency graph stalled",
  routing_error: "Routing error",
  escalated: "Escalated",
  frozen_chain: "Frozen dependency chain",
  credential: "Credential problem",
  unarchived: "Restored from archive",
};

function BlockerItem({ blocker }: Readonly<{ blocker: Blocker }>) {
  return (
    <li data-testid="blocker-item" className="text-xs text-text-primary">
      <span
        data-testid="blocker-kind"
        className="mr-2 rounded bg-white/10 px-1.5 py-0.5 font-medium"
      >
        {blocker.kind}
      </span>
      <span data-testid="blocker-name" className="font-medium">
        {blocker.name}
      </span>
      {": "}
      <span data-testid="blocker-detail">{blocker.detail}</span>
    </li>
  );
}

function ParkReasonFact({
  reason,
  blockers,
}: Readonly<{ reason: ParkReason | null; blockers: readonly Blocker[] }>) {
  if (reason === null && blockers.length === 0) return null;
  return (
    <section data-testid="park-reason-panel" className="space-y-2">
      {reason !== null && (
        <p className="text-sm">
          <span className="font-medium">Why it stopped:</span>{" "}
          <span data-testid="park-reason-label">{PARK_REASON_LABELS[reason]}</span>
        </p>
      )}
      {blockers.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">Blockers</h3>
          <ul className="space-y-1">
            {blockers.map((blocker, i) => (
              <BlockerItem key={`${blocker.kind}-${blocker.name}-${i}`} blocker={blocker} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function TerminalBlockClassFact({ outcome }: Readonly<{ outcome: TerminalOutcome | null }>) {
  if (outcome?.block_class == null || outcome.block_class === "") return null;
  return (
    <p className="text-xs text-text-muted">
      <span
        data-testid="merge-block-class"
        className="rounded bg-white/10 px-1.5 py-0.5 font-medium"
      >
        {formatMergeBlockLabel(outcome.block_class)}
      </span>
    </p>
  );
}

function pendingExternalAnswerOf(question: OpenQuestion): boolean {
  const answer = question.answer ?? null;
  if (answer === null) return false;
  return question.option_effects?.[answer] === "pending_external";
}

interface PendingExternalCardProps {
  readonly taskId: string;
  readonly question: OpenQuestion;
  readonly latest: Handover;
  readonly answer: string;
  readonly onRefresh: () => void;
}

function PendingExternalCard({
  taskId,
  question,
  latest,
  answer,
  onRefresh,
}: Readonly<PendingExternalCardProps>) {
  const { busy, run } = useAction();
  const [error, setError] = useState<string | null>(null);

  const handleRestart = (): void => {
    const team = question.option_resolvers?.[answer] ?? latest.from_team;
    setError(null);
    run(() => api.dispatchTask(taskId, latest.task_id, team, {}))
      .then(() => onRefresh())
      .catch((err: unknown) => setError(errorMessageOf(err)));
  };

  return (
    <div className="space-y-2">
      <output
        data-testid="decision-card-pending-external"
        className="block rounded-md bg-status-attention-tint/15 px-3 py-2 text-xs text-status-attention-fg"
      >
        Waiting on you to complete this outside the fleet. Nothing was dispatched and nothing is
        running. Restart the check once the step has landed.
      </output>
      <Button
        variant="action"
        onClick={handleRestart}
        disabled={busy}
        className="min-h-11"
        data-testid="decision-card-restart-check"
      >
        {busy ? "Restarting…" : "Restart the check"}
      </Button>
      {error !== null && (
        <p role="alert" className="text-xs text-btn-destroy">
          {error}
        </p>
      )}
    </div>
  );
}

interface DecisionFactProps {
  readonly decision: SituationDecision | null;
  readonly taskId: string;
  readonly onRefresh: () => void;
}

function DecisionFact({ decision, taskId, onRefresh }: Readonly<DecisionFactProps>) {
  if (decision === null) return null;
  const { question, latest } = decision;
  const showPendingExternal = pendingExternalAnswerOf(question) && question.answer != null;
  return (
    <section data-testid="decision-card" className="space-y-2">
      <h3 className="text-sm font-medium">Blocking decision</h3>
      {showPendingExternal && question.answer != null && (
        <PendingExternalCard
          taskId={taskId}
          question={question}
          latest={latest}
          answer={question.answer}
          onRefresh={onRefresh}
        />
      )}
      <OpenQuestionsSection questions={[question]} />
    </section>
  );
}

const UNBLOCK_STATE_LABEL: Record<UnblockDisplayState, string> = {
  waiting: "Host is waiting and re-probing",
  acted: "Host acted",
  needs_human: "Needs you",
};

const UNBLOCK_STATE_GLYPH: Record<UnblockDisplayState, string> = {
  waiting: "∼",
  acted: "✓",
  needs_human: "!",
};

function useNowAtSilenceBoundary(record: UnblockRecord): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!isHostOwnedOutcome(record.outcome)) return;
    const deadlineMs = record.at * 1000 + SILENCE_THRESHOLD_MS;
    const delayMs = deadlineMs - Date.now();
    if (delayMs <= 0) return;
    const timer = globalThis.setTimeout(() => setNowMs(Date.now()), delayMs + 1);
    return () => globalThis.clearTimeout(timer);
  }, [record.at, record.outcome]);
  return nowMs;
}

function unblockWhatHappened(
  record: UnblockRecord,
  displayState: UnblockDisplayState,
  silent: boolean,
): string {
  if (displayState === "acted") {
    return record.action != null ? `Action taken: ${record.action}` : record.summary;
  }
  if (displayState === "waiting") {
    return record.reason != null ? `Waiting on: ${record.reason}` : "Waiting and re-probing.";
  }
  if (silent) {
    return `No action or report for over ${String(SILENCE_THRESHOLD_MINUTES)} minutes — treated as needing you.`;
  }
  return record.reason != null ? `Reported: ${record.reason}` : record.summary;
}

function UnblockFactBody({ record }: Readonly<{ record: UnblockRecord }>) {
  const nowMs = useNowAtSilenceBoundary(record);
  const view: UnblockView = unblockView(record, nowMs);
  const showEscalateNote = view.displayState === "waiting" || view.displayState === "acted";
  return (
    <output
      aria-label="Autonomous unblocker status"
      data-testid="unblock-notice"
      className="block space-y-1 text-sm"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium" data-testid="unblock-state-label">
          {UNBLOCK_STATE_LABEL[view.displayState]}
        </span>
        <span aria-hidden="true">{UNBLOCK_STATE_GLYPH[view.displayState]}</span>
        <span
          className="rounded bg-white/10 px-1.5 py-0.5 text-xs font-medium"
          data-testid="unblock-shape"
        >
          {record.shape}
        </span>
      </div>
      <p data-testid="unblock-what-happened">
        {unblockWhatHappened(record, view.displayState, view.silent)}
      </p>
      <p className="text-xs" data-testid="unblock-attempts">
        Attempt {record.attempts} of {record.ceiling}
      </p>
      {view.displayState === "needs_human" && !view.silent && (
        <p className="text-xs" data-testid="unblock-authority-holder">
          This needs {authorityHolderLabel(record.authority_holder)}.
        </p>
      )}
      {record.issue_url != null && (
        <a
          href={record.issue_url}
          target="_blank"
          rel="noreferrer"
          className="inline-block text-xs underline"
          data-testid="unblock-issue-link"
        >
          Tracking issue →
        </a>
      )}
      <p className="text-xs text-text-muted" data-testid="unblock-updated">
        Updated {formatRelativeTime(new Date(record.at * 1000).toISOString())}
        {showEscalateNote &&
          ` · escalates to you after ${String(SILENCE_THRESHOLD_MINUTES)} minutes of silence`}
      </p>
    </output>
  );
}

function UnblockFact({ record }: Readonly<{ record: UnblockRecord | null }>) {
  if (record === null) return null;
  return <UnblockFactBody record={record} />;
}

function ManagerSummaryFact({ summary }: Readonly<{ summary: ManagerSummary | null }>) {
  if (summary == null) return null;
  if (!summary.ok || summary.text == null) {
    return (
      <p data-testid="manager-summary-unavailable" className="text-xs text-text-muted">
        What happened: summary unavailable
      </p>
    );
  }
  return (
    <section data-testid="manager-summary-panel" className="space-y-1">
      <h3 className="text-sm font-medium">What happened</h3>
      <p className={`text-sm ${summary.stale ? "text-text-muted" : "text-text-primary"}`}>
        {summary.text}
      </p>
      {summary.written_at != null && (
        <p className="text-xs text-text-muted" data-testid="manager-summary-written-at">
          Written {formatDate(summary.written_at)}
        </p>
      )}
      {summary.stale && (
        <p className="text-xs text-status-attention-fg" data-testid="manager-summary-stale">
          The workspace has moved on since this was written.
        </p>
      )}
    </section>
  );
}

function backstopSeconds(gate: PendingDeployGate): number | null {
  return gate.deadline ?? gate.race_deadline;
}

function DeployGateFact({ gate }: Readonly<{ gate: PendingDeployGate }>) {
  const backstop = backstopSeconds(gate);
  return (
    <div
      id="deploy-gate-detail"
      className="text-xs text-text-muted"
      data-testid="deploy-gate-waited-block"
    >
      <span data-testid="pending-deploy-gate-waited">
        Waited {formatActiveDuration(gate.waited_s)} so far — repeated check-ins here are
        expected, not a stuck job.
      </span>
      {backstop !== null && (
        <span data-testid="pending-deploy-gate-backstop" className="block">
          Will escalate automatically around{" "}
          {formatDate(new Date(backstop * 1000).toISOString())} if this never resolves.
        </span>
      )}
    </div>
  );
}

function FilesChangedFact({ count }: Readonly<{ count: number }>) {
  if (count === 0) return null;
  return (
    <p className="text-xs text-text-muted" data-testid="situation-files-changed">
      {count} file{count === 1 ? "" : "s"} changed
    </p>
  );
}

function CommitsFact({ commits }: Readonly<{ commits: readonly { sha: string; message: string }[] }>) {
  if (commits.length === 0) return null;
  return (
    <ul data-testid="situation-commits" className="space-y-0.5">
      {commits.map((c) => (
        <li key={c.sha} className="text-xs">
          <code>{c.sha.slice(0, 7)}</code> {c.message}
        </li>
      ))}
    </ul>
  );
}

function DeliveryBody({ delivery }: Readonly<{ delivery: WorkspaceDelivery }>) {
  const prUrl = delivery.pr_url ?? pullUrl(delivery.repo, delivery.pr_number);
  return (
    <div data-testid="situation-delivery" className="space-y-2">
      <a
        href={prUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm text-btn-action hover:underline"
        data-testid="situation-pr-title-link"
      >
        {delivery.pr_title ?? `#${String(delivery.pr_number)}`}
      </a>
      <FilesChangedFact count={delivery.files_changed?.length ?? 0} />
      <CommitsFact commits={delivery.commits ?? []} />
      <a
        href={prUrl}
        target="_blank"
        rel="noopener noreferrer"
        data-testid="situation-open-pr-link"
        className="inline-flex min-h-11 items-center text-sm text-btn-action underline"
      >
        Open PR →
      </a>
    </div>
  );
}

function DeliveryFact({ taskId }: Readonly<{ taskId: string }>) {
  const view = useDocQuery("workspace-delivery", taskId, (id) => api.getDelivery(id));
  if (view.isError) {
    return (
      <div role="alert" data-testid="situation-delivery-error">
        Failed to load delivery summary: {errorMessageOf(view.error)}
      </div>
    );
  }
  if (view.data === undefined) {
    return (
      <p className="text-xs text-text-muted" data-testid="situation-delivery-loading">
        Loading delivery summary…
      </p>
    );
  }
  return <DeliveryBody delivery={view.data} />;
}

export interface SituationFactsProps {
  readonly situation: Situation;
  readonly taskId: string;
  readonly onRefresh: () => void;
}

export default function SituationFacts({
  situation,
  taskId,
  onRefresh,
}: Readonly<SituationFactsProps>) {
  return (
    <div data-testid="situation-facts" className="space-y-3">
      <StickyNoticeFact notice={situation.stickyNotice} />
      <ActivityFeed notices={situation.activityNotices} />
      {situation.state === "deploy_wait" && <DeployGateFact gate={situation.deployGate!} />}
      <TerminalBlockClassFact outcome={situation.terminalOutcome} />
      <ParkReasonFact reason={situation.parkReason} blockers={situation.blockers} />
      <DecisionFact decision={situation.decision} taskId={taskId} onRefresh={onRefresh} />
      <UnblockFact record={situation.unblock} />
      <ManagerSummaryFact summary={situation.managerSummary} />
      {situation.state === "completed" && <DeliveryFact taskId={taskId} />}
    </div>
  );
}
