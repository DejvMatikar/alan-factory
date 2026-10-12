import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

import Button from "../../../components/Button";
import type { MaintenanceOutcome } from "../hooks/useRepoActions";

export interface RepoActionRowProps {
  readonly repoKey: string;
  readonly maintenanceOutcome: MaintenanceOutcome | null;
  readonly maintenanceStopping: boolean;
  readonly onStopMaintenance: () => void;
}

interface MaintenanceNoticeProps {
  readonly repoKey: string;
  readonly outcome: MaintenanceOutcome;
  readonly stopping: boolean;
  readonly onStop: () => void;
}

function MaintenanceNotice({ repoKey, outcome, stopping, onStop }: Readonly<MaintenanceNoticeProps>) {
  const testId = `repo-maintenance-notice-${repoKey}`;
  const noticeRef = useRef<HTMLOutputElement>(null);
  const previousKindRef = useRef(outcome.kind);
  useEffect(() => {
    // Stop unmounts the button whose focus it held; land focus on the
    // notice itself so a keyboard/screen-reader user isn't dropped to body.
    if (previousKindRef.current === "running" && outcome.kind === "stopped") {
      noticeRef.current?.focus();
    }
    previousKindRef.current = outcome.kind;
  }, [outcome.kind]);
  if (outcome.kind === "error") {
    return (
      <output data-testid={testId} className="w-full text-xs text-btn-destroy">
        Maintenance failed to start — {outcome.detail}
      </output>
    );
  }
  return (
    <output ref={noticeRef} tabIndex={-1} data-testid={testId} className="w-full text-xs text-text-muted">
      Maintenance started —{" "}
      <Link
        to={`/workspaces/${encodeURIComponent(outcome.taskId)}`}
        className="text-btn-action underline"
      >
        view workspace
      </Link>
      {outcome.kind === "running" ? (
        <>
          {" · "}
          <Button
            variant="ghost"
            data-testid={`repo-maintenance-stop-${repoKey}`}
            onClick={onStop}
            disabled={stopping}
          >
            Stop
          </Button>
          {outcome.stopError !== undefined && (
            <span className="text-btn-destroy"> · Stop failed — {outcome.stopError}</span>
          )}
        </>
      ) : (
        " · Stopped."
      )}
    </output>
  );
}

export default function RepoActionRow({
  repoKey,
  maintenanceOutcome,
  maintenanceStopping,
  onStopMaintenance,
}: Readonly<RepoActionRowProps>) {
  return (
    <div className="flex w-full flex-col gap-2" data-testid={`repo-action-row-${repoKey}`}>
      {maintenanceOutcome !== null && (
        <MaintenanceNotice
          repoKey={repoKey}
          outcome={maintenanceOutcome}
          stopping={maintenanceStopping}
          onStop={onStopMaintenance}
        />
      )}
    </div>
  );
}
