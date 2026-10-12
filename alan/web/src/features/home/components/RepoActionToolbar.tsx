import { Link } from "react-router-dom";

import Button from "../../../components/Button";
import NewProjectModal from "../../../components/NewProjectModal";
import type { RepoSectionModel } from "../../../lib/fold";
import type { BindFailureReason, UseRepoActionsResult } from "../hooks/useRepoActions";

export interface RepoActionToolbarProps {
  readonly section: RepoSectionModel;
  readonly showProjects: boolean;
  readonly actions: UseRepoActionsResult;
}

function unboundReasonText(reason: BindFailureReason, detail: string): string {
  switch (reason) {
    case "preflight":
      return "this repository hasn't passed its connection preflight yet";
    case "live_workspaces":
      return "this repository has live workspaces blocking a rebind";
    case "unknown_engine":
      return "this repository's engine could not be found";
    default:
      return detail;
  }
}

export default function RepoActionToolbar({
  section,
  showProjects,
  actions,
}: Readonly<RepoActionToolbarProps>) {
  const { bindOutcome } = actions;

  return (
    <div className="flex flex-col gap-2" data-testid={`repo-action-toolbar-${section.key}`}>
      {showProjects && (
        <div>
          <Button
            variant="ghost"
            data-testid={`repo-start-project-${section.key}`}
            onClick={actions.openModal}
          >
            Start a project
          </Button>
        </div>
      )}
      {actions.modalOpen && (
        <NewProjectModal onClose={actions.closeModal} onCreated={actions.onProjectCreated} />
      )}
      {showProjects && bindOutcome !== null && (
        <output
          data-testid={`repo-bind-notice-${section.key}`}
          className="w-full text-xs text-text-muted"
        >
          <Link
            to={`/projects/${encodeURIComponent(bindOutcome.project.id)}`}
            className="text-btn-action underline"
          >
            {bindOutcome.project.title}
          </Link>{" "}
          {bindOutcome.kind === "bound"
            ? "was created and bound to this repository."
            : `was created but is not yet bound to this repository — ${unboundReasonText(bindOutcome.reason, bindOutcome.detail)}.`}
        </output>
      )}
    </div>
  );
}
