import { useCallback, useState, type KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, type Project, type ProjectListEntry, type ProjectStatus } from "../../../api";
import { useDocQuery } from "../../../api/queries";
import Button from "../../../components/Button";
import NewProjectModal from "../../../components/NewProjectModal";
import { errorMessageOf } from "../../../lib/errorMessage";
import ErrorBanner from "../../../ui/ErrorBanner";

const STATUS_LABELS: Record<ProjectStatus, string> = {
  active: "active",
  archived: "archived",
  completed: "shipped",
  provisioning: "provisioning",
};

const STATUS_STYLES: Record<ProjectStatus, string> = {
  active: "bg-status-active-tint/15 text-status-active-fg",
  archived: "bg-status-neutral-tint/15 text-status-neutral-fg",
  completed: "bg-status-success-tint/15 text-status-success-fg",
  provisioning: "bg-status-attention-tint/15 text-status-attention-fg",
};

export function ProjectStatusBadge({ status }: Readonly<{ status: ProjectStatus }>) {
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs ${STATUS_STYLES[status]}`}
      data-testid={`project-status-${status}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

function formatSpendUsd(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

type ProjectTab = "active" | "archived";

const PROJECT_TABS: ProjectTab[] = ["active", "archived"];
const PROJECT_TAB_SESSION_KEY = "project-list-tab";

function readStoredProjectTab(): ProjectTab {
  const stored = sessionStorage.getItem(PROJECT_TAB_SESSION_KEY);
  return stored === "archived" ? "archived" : "active";
}

const PROJECT_TAB_LABELS: Record<ProjectTab, string> = {
  active: "Active",
  archived: "Archived",
};

const PROJECT_TAB_EMPTY_MESSAGES: Record<ProjectTab, string> = {
  active: "No active projects yet.",
  archived: "No archived projects yet.",
};

function filterProjectsByTab(
  projects: readonly ProjectListEntry[],
  tab: ProjectTab,
): readonly ProjectListEntry[] {
  if (tab === "archived") return projects.filter((p) => p.status === "archived");
  return projects.filter((p) => p.status !== "archived");
}

interface ProjectTabsProps {
  tab: ProjectTab;
  onSelect: (tab: ProjectTab) => void;
}

function ProjectTabs({ tab, onSelect }: Readonly<ProjectTabsProps>) {
  const handleKeyDown = useCallback(
    // Only two tabs exist, so clamping at either end collapses to "ArrowRight
    // always lands on archived, ArrowLeft always lands on active" — no index
    // arithmetic on the current tab is needed.
    (e: KeyboardEvent<HTMLDivElement>): void => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const nextTab: ProjectTab = e.key === "ArrowRight" ? "archived" : "active";
      onSelect(nextTab);
      const nextIndex = PROJECT_TABS.indexOf(nextTab);
      const nextTabButton =
        e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')[nextIndex];
      /* v8 ignore next */
      nextTabButton?.focus();
    },
    [onSelect],
  );

  return (
    <div role="tablist" tabIndex={-1} className="flex gap-2" onKeyDown={handleKeyDown}>
      {PROJECT_TABS.map((t) => (
        <button
          key={t}
          type="button"
          role="tab"
          aria-selected={tab === t}
          tabIndex={tab === t ? 0 : -1}
          data-testid={`project-tab-${t}`}
          onClick={() => onSelect(t)}
          className={
            tab === t
              ? "min-h-11 rounded-md bg-surface-elevated px-3 py-1.5 text-sm font-medium text-text-primary"
              : "min-h-11 rounded-md px-3 py-1.5 text-sm font-medium text-text-muted hover:text-text-primary"
          }
        >
          {PROJECT_TAB_LABELS[t]}
        </button>
      ))}
    </div>
  );
}

const PROJECTS_NAMESPACE = "projects-list";
const ALL_PROJECTS_KEY = "all";

export default function Projects() {
  const navigate = useNavigate();
  const view = useDocQuery(PROJECTS_NAMESPACE, ALL_PROJECTS_KEY, () =>
    api.listProjects({ includeArchived: true }),
  );
  const projects = view.data ?? null;
  const loadError = view.isError ? errorMessageOf(view.error) : null;
  const [showNewProjectModal, setShowNewProjectModal] = useState(false);
  const [tab, setTab] = useState<ProjectTab>(readStoredProjectTab);

  const selectTab = useCallback((t: ProjectTab): void => {
    sessionStorage.setItem(PROJECT_TAB_SESSION_KEY, t);
    setTab(t);
  }, []);

  const handleCreated = (project: Project): void => {
    setShowNewProjectModal(false);
    void navigate(`/projects/${encodeURIComponent(project.id)}`);
  };

  return (
    <div className="space-y-6" data-testid="projects-page">
      {showNewProjectModal && (
        <NewProjectModal
          onClose={() => setShowNewProjectModal(false)}
          onCreated={handleCreated}
        />
      )}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text-primary">Projects</h1>
        <Button
          variant="create"
          data-testid="new-project-button"
          onClick={() => setShowNewProjectModal(true)}
        >
          New project
        </Button>
      </div>

      <ProjectTabs tab={tab} onSelect={selectTab} />

      <ProjectsList
        projects={projects === null ? null : filterProjectsByTab(projects, tab)}
        loadError={loadError}
        emptyMessage={PROJECT_TAB_EMPTY_MESSAGES[tab]}
      />
    </div>
  );
}

interface ProjectsListProps {
  projects: readonly ProjectListEntry[] | null;
  loadError: string | null;
  emptyMessage: string;
}

function ProjectsList({ projects, loadError, emptyMessage }: Readonly<ProjectsListProps>) {
  if (loadError !== null) {
    return <ErrorBanner>{loadError}</ErrorBanner>;
  }
  if (projects === null) {
    return (
      <p className="text-sm text-text-muted" data-testid="projects-loading">
        Loading…
      </p>
    );
  }
  if (projects.length === 0) {
    return (
      <p className="text-sm text-text-muted" data-testid="projects-empty">
        {emptyMessage}
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border" data-testid="projects-list">
      {projects.map((p) => (
        <li key={p.id} className="flex items-center justify-between gap-2 py-3">
          <div className="flex flex-col">
            <Link
              to={`/projects/${encodeURIComponent(p.id)}`}
              className="font-medium text-text-primary hover:underline"
            >
              {p.title}
            </Link>
            <span className="text-xs text-text-muted" data-testid={`project-stats-${p.id}`}>
              {p.workspace_count} workspace{p.workspace_count === 1 ? "" : "s"} ·{" "}
              {formatSpendUsd(p.total_spend_usd)}
            </span>
          </div>
          <ProjectStatusBadge status={p.status} />
        </li>
      ))}
    </ul>
  );
}
