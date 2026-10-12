import { Link, useNavigate } from "react-router-dom";

import type { WorkspaceDetail } from "../../../api";
import EngineBadge from "../../../components/EngineBadge";
import GithubRef from "../../../components/GithubRef";
import StatusBadge from "../../../components/StatusBadge";
import { useEngines } from "../../../hooks/useEngines";
import { formatDate } from "../../../lib/format";
import type { WorkspaceLifecycle } from "../hooks/useWorkspaceLifecycle";
import DeleteWorkspaceDialog from "./DeleteWorkspaceDialog";
import WorkspaceHeaderActions from "./WorkspaceHeaderActions";

const PR_LINK_CLASS =
  "inline-flex min-h-11 items-center text-xs font-medium text-btn-action underline";

function LiveSince({ liveAt }: Readonly<{ liveAt: string | null | undefined }>) {
  if (liveAt == null) return null;
  return (
    <span data-testid="workspace-live-since" className="text-xs text-text-muted">
      live since {formatDate(liveAt)}
    </span>
  );
}

function PrNumberLink({
  prNumber,
  engineKey,
}: Readonly<{ prNumber: number | null | undefined; engineKey: string | null | undefined }>) {
  if (prNumber == null) return null;
  return (
    <GithubRef
      repo={engineKey ?? null}
      kind="pull"
      prNumber={prNumber}
      className={PR_LINK_CLASS}
      rel="noopener noreferrer"
      testId="workspace-pr-link"
    >
      PR #{prNumber}
    </GithubRef>
  );
}

function OperatorLink({ show, taskId }: Readonly<{ show: boolean; taskId: string }>) {
  if (!show) return null;
  return (
    <Link
      to={`/operator?scope=workspace&target=${encodeURIComponent(taskId)}`}
      className={PR_LINK_CLASS}
      data-testid="workspace-operator-link"
    >
      Open in operator terminal
    </Link>
  );
}

export interface WorkspaceHeaderProps {
  readonly ws: WorkspaceDetail;
  readonly taskId: string;
  readonly lifecycle: WorkspaceLifecycle;
}

export default function WorkspaceHeader({
  ws,
  taskId,
  lifecycle,
}: Readonly<WorkspaceHeaderProps>) {
  const navigate = useNavigate();
  const { allEngines, defaultEngineKey } = useEngines();
  const hasNonDefaultEngine = allEngines.some(
    (e) => `${e.repo.owner}/${e.repo.repo}` !== defaultEngineKey,
  );
  const repoKey = ws.engine_key ?? defaultEngineKey;

  return (
    <div className="space-y-2">
      {repoKey !== null && (
        <Link
          to={`/repos/${repoKey}`}
          className="inline-flex min-h-11 items-center text-xs text-text-muted hover:text-text-primary"
          data-testid="workspace-back-link"
        >
          ← {repoKey}
        </Link>
      )}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2 sm:min-h-11">
          <h1 className="text-xl font-medium text-text-primary">{ws.title || ws.task_id}</h1>
          <StatusBadge status={ws.status} />
          {hasNonDefaultEngine && (
            <EngineBadge engineKey={ws.engine_key ?? null} testIdSuffix={ws.task_id} />
          )}
          <PrNumberLink prNumber={ws.pr_number} engineKey={ws.engine_key} />
          <LiveSince liveAt={ws.live_at} />
          <OperatorLink show={ws.operator_link} taskId={ws.task_id} />
        </div>
        <WorkspaceHeaderActions
          stopResumeMode={lifecycle.stopResumeMode}
          stopResumeBusy={lifecycle.stopResumeBusy}
          onStopResumeClick={lifecycle.handleStopResume}
          requeueCount={ws.requeue_count}
          isArchived={ws.status === "archived"}
          toggleBusy={lifecycle.toggleBusy}
          pendingIsArchived={lifecycle.pendingIsArchived}
          onToggleClick={lifecycle.handleToggle}
          onDeleteClick={lifecycle.openDelete}
        />
      </div>
      {lifecycle.deleteOpen && (
        <DeleteWorkspaceDialog
          taskId={taskId}
          onClose={lifecycle.closeDelete}
          onDeleted={() => {
            lifecycle.closeDelete();
            void navigate("/");
          }}
        />
      )}
    </div>
  );
}
