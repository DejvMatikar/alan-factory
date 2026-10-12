import type { WorkspaceDetail } from "../../../api";
import Button from "../../../components/Button";
import { situationOf, type SituationPrimaryAction, type SituationState } from "../hooks/situation";
import type { WorkspaceLifecycle } from "../hooks/useWorkspaceLifecycle";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import SituationFacts from "./SituationFacts";

interface PrimaryActionInfo {
  readonly idle: string;
  readonly busy: string;
  readonly testId: string;
}

const PRIMARY_ACTION_INFO: Record<NonNullable<SituationPrimaryAction>, PrimaryActionInfo> = {
  stop: { idle: "Stop", busy: "Stopping…", testId: "situation-stop-button" },
  resume: { idle: "Resume", busy: "Resuming…", testId: "situation-resume-button" },
  requeue: { idle: "Re-queue", busy: "Re-queuing…", testId: "situation-requeue-button" },
};

interface SituationPrimaryActionButtonProps {
  readonly action: NonNullable<SituationPrimaryAction>;
  readonly busy: boolean;
  readonly onClick: () => void;
}

function SituationPrimaryActionButton({
  action,
  busy,
  onClick,
}: Readonly<SituationPrimaryActionButtonProps>) {
  const info = PRIMARY_ACTION_INFO[action];
  return (
    <Button variant="action" onClick={onClick} busy={busy} data-testid={info.testId}>
      {busy ? info.busy : info.idle}
    </Button>
  );
}

function SituationBody({
  state,
  text,
}: Readonly<{ state: SituationState; text: string }>) {
  if (state === "deploy_wait") {
    return (
      <output
        data-testid="pending-deploy-gate-notice"
        aria-label="Deploy verification status"
        aria-describedby="deploy-gate-detail"
        className="block text-sm text-text-primary"
      >
        {text}
      </output>
    );
  }
  return (
    <p data-testid="situation-body" className="text-sm text-text-primary">
      {text}
    </p>
  );
}

export interface SituationCardProps {
  readonly ws: WorkspaceDetail;
  readonly taskId: string;
  readonly live: LiveWorkspaceState;
  readonly lifecycle: WorkspaceLifecycle;
  readonly onRefresh: () => void;
}

export default function SituationCard({
  ws,
  taskId,
  live,
  lifecycle,
  onRefresh,
}: Readonly<SituationCardProps>) {
  const situation = situationOf(ws, live);
  return (
    <section
      data-testid="situation-card"
      aria-label="Workspace situation"
      className="space-y-3 rounded-lg border border-border bg-surface-elevated p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2
          className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-base font-medium ${situation.toneClass}`}
          data-testid="situation-headline"
        >
          {situation.headline}
          {situation.glyph != null && <span aria-hidden="true">{situation.glyph}</span>}
        </h2>
      </div>
      <SituationBody state={situation.state} text={situation.body} />
      <SituationFacts situation={situation} taskId={taskId} onRefresh={onRefresh} />
      {situation.primaryAction !== null && (
        <SituationPrimaryActionButton
          action={situation.primaryAction}
          busy={lifecycle.stopResumeBusy}
          onClick={lifecycle.handleStopResume}
        />
      )}
    </section>
  );
}
