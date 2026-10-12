import DescribedButton from "../../../components/DescribedButton";
import DescribedIconButton from "../../../components/DescribedIconButton";
import { TrashIcon } from "../../../components/Button";

export type StopResumeMode = "stop" | "resume" | "requeue";

interface StopResumeInfo {
  readonly idle: string;
  readonly busy: string;
  readonly description: string;
  readonly testId: string;
}

const STOP_RESUME_INFO: Record<StopResumeMode, StopResumeInfo> = {
  stop: {
    idle: "Stop",
    busy: "Stopping…",
    description: "Stops the current run immediately. You can resume it afterward.",
    testId: "stop-button",
  },
  resume: {
    idle: "Resume",
    busy: "Resuming…",
    description: "Starts a new run to continue this workspace from where it paused.",
    testId: "resume-button",
  },
  requeue: {
    idle: "Re-queue",
    busy: "Re-queuing…",
    description: "Retries the failed run so the workspace can continue.",
    testId: "requeue-button",
  },
};

interface StopResumeGroupProps {
  readonly mode: StopResumeMode;
  readonly busy: boolean;
  readonly onClick: () => void;
  readonly requeueCount: number;
}

function StopResumeGroup({ mode, busy, onClick, requeueCount }: Readonly<StopResumeGroupProps>) {
  const info = STOP_RESUME_INFO[mode];
  return (
    <>
      <DescribedButton
        variant="ghost"
        label={busy ? info.busy : info.idle}
        description={info.description}
        testId={info.testId}
        onClick={onClick}
        busy={busy}
      />
      {mode !== "stop" && requeueCount > 0 && (
        <span data-testid="requeue-count" className="text-xs text-text-muted">
          retried {requeueCount}×
        </span>
      )}
    </>
  );
}

interface ArchiveInfo {
  readonly idle: string;
  readonly busy: string;
  readonly description: string;
}

function archiveInfoFor(isArchived: boolean): ArchiveInfo {
  if (isArchived) {
    return {
      idle: "Unarchive",
      busy: "Unarchiving…",
      description: "Returns this workspace to the active list.",
    };
  }
  return {
    idle: "Archive",
    busy: "Archiving…",
    description: "Moves this workspace out of the active list. You can unarchive it later.",
  };
}

const DELETE_DESCRIPTION =
  "Permanently removes this workspace and its history. This cannot be undone.";

export interface WorkspaceHeaderActionsProps {
  readonly stopResumeMode: StopResumeMode | null;
  readonly stopResumeBusy: boolean;
  readonly onStopResumeClick: () => void;
  readonly requeueCount: number;
  readonly isArchived: boolean;
  readonly toggleBusy: boolean;
  readonly pendingIsArchived: boolean;
  readonly onToggleClick: () => void;
  readonly onDeleteClick: () => void;
}

export default function WorkspaceHeaderActions({
  stopResumeMode,
  stopResumeBusy,
  onStopResumeClick,
  requeueCount,
  isArchived,
  toggleBusy,
  pendingIsArchived,
  onToggleClick,
  onDeleteClick,
}: Readonly<WorkspaceHeaderActionsProps>) {
  const archive = archiveInfoFor(toggleBusy ? pendingIsArchived : isArchived);
  return (
    <fieldset
      aria-label="Workspace actions"
      className="m-0 flex flex-wrap items-center gap-2 border-0 p-0"
    >
      {stopResumeMode !== null && (
        <StopResumeGroup
          mode={stopResumeMode}
          busy={stopResumeBusy}
          onClick={onStopResumeClick}
          requeueCount={requeueCount}
        />
      )}
      <DescribedButton
        variant="ghost"
        label={toggleBusy ? archive.busy : archive.idle}
        description={archive.description}
        testId="archive-button"
        onClick={onToggleClick}
        busy={toggleBusy}
      />
      <DescribedIconButton
        variant="destroy"
        glyph={<TrashIcon />}
        label="Delete"
        description={DELETE_DESCRIPTION}
        testId="delete-button"
        onClick={onDeleteClick}
        align="end"
      />
    </fieldset>
  );
}
