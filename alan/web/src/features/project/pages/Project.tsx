import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import {
  api,
  type BudgetState,
  type Project as ProjectFields,
  type ProjectAutonomy,
  type ProjectBudgetView,
  type ProjectDetail as ProjectDetailData,
  type ProjectDoc,
  type ProjectLedgerEntry,
  type ProjectStatus,
  type RoadmapItemView,
} from "../../../api";
import { useDocQuery, useRefetchDoc, useSetDocData } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";
import Button from "../../../components/Button";
import { DraftRowItem, useDraftRows } from "../../../components/ChatDraftRows";
import ConnectUnboundNotice, { readConnectUnboundState } from "../../../components/ConnectUnboundNotice";
import EngineOpsPanel from "../../../components/EngineOpsPanel";
import GithubRef from "../../../components/GithubRef";
import ImportRoadmapModal from "../components/ImportRoadmapModal";
import { blobBaseUrl } from "../../../lib/githubLinks";
import MarkdownPanel from "../../../components/MarkdownPanel";
import ProposeRoadmapChangesModal from "../components/ProposeRoadmapChangesModal";
import RoadmapStatusBadge from "../components/StatusBadge";
import { formatDate } from "../../../lib/format";
import { useProjectDoc, type UseProjectDocResult } from "../hooks/useProjectDoc";
import DialogFrame from "../../../ui/DialogFrame";
import ErrorBanner from "../../../ui/ErrorBanner";
import Input from "../../../ui/Input";
import SectionHeading from "../../../ui/SectionHeading";
import { useAction } from "../../../ui/useAction";
import { useDefaultEngineState, type DefaultEngineState } from "../../../hooks/useEngines";
import { ProjectStatusBadge } from "./Projects";
import NextItemProposal from "../components/NextItemProposal";
import StatusBadge from "../../../components/StatusBadge";

function formatUsd(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

function parseBudgetInput(raw: string): number | null | "invalid" {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed < 0) return "invalid";
  return parsed;
}

interface AutonomyBudgetEditorProps {
  project: ProjectFields;
  onSaved: (updated: ProjectFields) => void;
}

function AutonomyBudgetEditor({ project, onSaved }: Readonly<AutonomyBudgetEditorProps>) {
  const [autonomy, setAutonomy] = useState<ProjectAutonomy>(project.autonomy);
  const [budgetInput, setBudgetInput] = useState(
    project.budget_usd == null ? "" : String(project.budget_usd),
  );
  const { busy: saving, run } = useAction();
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = (): void => {
    const budget_usd = parseBudgetInput(budgetInput);
    if (budget_usd === "invalid") {
      setError("Budget must be a non-negative number.");
      setSaved(false);
      return;
    }
    setError(null);
    setSaved(false);
    run(() => api.updateProject(project.id, { autonomy, budget_usd }))
      .then((updated) => {
        setSaved(true);
        onSaved(updated);
      })
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  return (
    <div className="space-y-3" data-testid="project-autonomy-editor">
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="project-autonomy-select">
          Autonomy
        </label>
        <Input
          as="select"
          id="project-autonomy-select"
          data-testid="project-autonomy-select"
          value={autonomy}
          onChange={(e) => {
            setAutonomy(e.target.value as ProjectAutonomy);
          }}
          disabled={saving}
        >
          <option value="propose">Propose — pause for my approval</option>
          <option value="auto_continue">Auto-continue</option>
        </Input>
        <p className="text-xs text-text-muted" data-testid="project-autonomy-help">
          Propose mode pauses for your approval: once every started roadmap item has
          delivered, a recommendation appears in the Roadmap section below for you to
          start with one tap. Auto-continue dispatches this project's next step without
          waiting for your approval, but only to execution teams (implementation, implementation_fix,
          verification, pr_merge, deploy_verify) — it never resolves a decision that needs
          you.
        </p>
      </div>
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="project-budget-input">
          Budget (USD)
        </label>
        <Input
          as="input"
          id="project-budget-input"
          data-testid="project-budget-input"
          type="text"
          inputMode="decimal"
          value={budgetInput}
          onChange={(e) => {
            setBudgetInput(e.target.value);
          }}
          disabled={saving}
          placeholder="No budget set"
          placeholderStyle
        />
      </div>
      {error !== null && (
        <p role="alert" data-testid="project-autonomy-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2">
        <Button
          variant="action"
          data-testid="project-autonomy-save"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
        {saved && (
          <span className="text-xs text-text-muted" data-testid="project-autonomy-saved">
            Saved.
          </span>
        )}
      </div>
    </div>
  );
}

interface BudgetStateSpec {
  label: string;
  pillClass: string;
  message: string;
}

// The panel communicates state through this one lookup rather than a
// meter/progress-bar — each row's `message` alone must say what the state
// means (WCAG 1.4.1: never colour-only), so `pillClass` only ever supplies
// a reused text-colour token (the muted default, or the --color-btn-* reset
// / destroy tokens), never the sole signal.
const BUDGET_STATE_VIEW: Record<BudgetState, BudgetStateSpec> = {
  normal: {
    label: "Normal",
    pillClass: "text-text-muted",
    message: "Spend is within budget.",
  },
  approaching: {
    label: "Approaching limit",
    pillClass: "text-btn-reset",
    message: "Spend is approaching this project's budget limit.",
  },
  paused: {
    label: "Paused",
    pillClass: "text-btn-destroy",
    message:
      "New work is paused because this project reached its budget. Workspaces already in flight will finish normally.",
  },
};

interface ProjectBurnViewProps {
  budget: ProjectBudgetView | null | undefined;
}

function ProjectBurnView({ budget }: Readonly<ProjectBurnViewProps>) {
  if (budget == null) {
    return (
      <div className="space-y-1 text-sm" data-testid="project-burn">
        <p role="alert" data-testid="project-burn-unavailable" className="text-btn-destroy">
          Spend data unavailable.
        </p>
      </div>
    );
  }
  const spec = BUDGET_STATE_VIEW[budget.state];
  return (
    <div className="space-y-1 text-sm" data-testid="project-burn">
      <p>
        <span className="font-medium text-text-primary">Total spend:</span>{" "}
        <span data-testid="project-burn-usd">{formatUsd(budget.burn_usd)}</span>
        {budget.budget_usd !== null && <> of {formatUsd(budget.budget_usd)} budget</>}
      </p>
      <p data-testid="project-budget-state" className="flex flex-wrap items-center gap-2">
        <span
          data-testid="project-budget-pill"
          className={`inline-block rounded px-2 py-0.5 text-xs ${spec.pillClass}`}
        >
          {spec.label}
        </span>
        <span>
          {budget.state !== "normal" && <span aria-hidden="true">⚠ </span>}
          {spec.message}
        </span>
      </p>
      <p className="text-xs italic text-text-muted" data-testid="project-burn-caption">
        Total spend across every category, including planning-chat and collision-check costs.
      </p>
    </div>
  );
}

const DOC_REF_URL_RE = /^https?:\/\//;

// docRef is user-provided (create/patch); `new URL(docRef, base)` treats any
// "scheme:" prefix (e.g. "javascript:...") as an absolute URL and ignores
// base, so the resolved protocol must be re-checked before use as an href.
// Callers only reach this for a docRef that already failed the absolute-URL
// test (DOC_REF_URL_RE), so it is never re-checked here.
function docRefHref(docRef: string, repo: string): string | null {
  let resolved: URL;
  try {
    resolved = new URL(docRef, blobBaseUrl(repo));
  } catch {
    return null;
  }
  return resolved.protocol === "http:" || resolved.protocol === "https:"
    ? resolved.toString()
    : null;
}

function WorkspacesSection({
  workspaces,
}: Readonly<{ workspaces: readonly ProjectLedgerEntry[] }>) {
  if (workspaces.length === 0) {
    return (
      <p className="text-sm text-text-muted" data-testid="project-workspaces-empty">
        No workspaces yet.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border" data-testid="project-workspaces-list">
      {workspaces.map((ws) => (
        <li key={ws.task_id} className="flex items-center justify-between gap-2 py-2">
          <Link
            to={`/workspaces/${encodeURIComponent(ws.task_id)}`}
            className="font-medium text-text-primary hover:underline"
          >
            {ws.title || ws.task_id}
          </Link>
          <StatusBadge status={ws.status} />
        </li>
      ))}
    </ul>
  );
}

function PullRequestsSection({
  workspaces,
  repo,
}: Readonly<{ workspaces: readonly ProjectLedgerEntry[]; repo: string | null }>) {
  const withPr = workspaces.filter(
    (ws): ws is ProjectLedgerEntry & { pr_number: number } => ws.pr_number != null,
  );
  if (withPr.length === 0) {
    return (
      <p className="text-sm text-text-muted" data-testid="project-prs-empty">
        No pull requests yet.
      </p>
    );
  }
  return (
    <ul className="space-y-1" data-testid="project-prs-list">
      {withPr.map((ws) => (
        <li key={ws.task_id}>
          <GithubRef
            repo={repo}
            kind="pull"
            prNumber={ws.pr_number}
            target="_blank"
            rel="noreferrer"
            className="text-btn-action underline"
            testId={`project-pr-link-${ws.task_id}`}
          >
            PR #{ws.pr_number} — {ws.title || ws.task_id}
          </GithubRef>
        </li>
      ))}
    </ul>
  );
}

function ConversationsSection({ projectId }: Readonly<{ projectId: string }>) {
  const { rows, loadError, removeRow } = useDraftRows(projectId);

  if (loadError !== null) {
    return (
      <p role="alert" data-testid="project-conversations-error" className="text-sm text-btn-destroy">
        {loadError}
      </p>
    );
  }

  if (rows === null) {
    return (
      <p className="text-sm text-text-muted" data-testid="project-conversations-loading">
        Loading conversations…
      </p>
    );
  }

  if (rows.length === 0) {
    return (
      <p className="text-sm text-text-muted" data-testid="project-conversations-empty">
        No conversations linked to this project yet.
      </p>
    );
  }

  return (
    <ul data-testid="project-conversations-list">
      {rows.map((r) => (
        <DraftRowItem key={r.sessionId} row={r} onDeleted={removeRow} showEngineMarker={false} />
      ))}
    </ul>
  );
}

function DocRefLink({ docRef, repo }: Readonly<{ docRef: string | null; repo: string | null }>) {
  if (docRef === null) return null;
  if (DOC_REF_URL_RE.test(docRef)) {
    return (
      <a
        href={docRef}
        target="_blank"
        rel="noreferrer"
        data-testid="project-doc-ref-link"
        className="text-sm text-btn-action underline"
      >
        {docRef}
      </a>
    );
  }
  if (repo === null) {
    return (
      <span
        data-testid="project-doc-ref-link"
        className="inline-block rounded px-2 py-0.5 text-xs bg-status-neutral-tint/15 text-status-neutral-fg"
      >
        {docRef} · repository unresolved
      </span>
    );
  }
  const href = docRefHref(docRef, repo);
  if (href === null) {
    return (
      <span data-testid="project-doc-ref-link" className="text-sm text-text-muted">
        {docRef}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      data-testid="project-doc-ref-link"
      className="text-sm text-btn-action underline"
    >
      {docRef}
    </a>
  );
}

interface ArchiveToggleButtonProps {
  projectId: string;
  status: ProjectStatus;
  onChanged: (updated: ProjectFields) => void;
}

function archiveToggleLabel(isArchived: boolean, busy: boolean): string {
  if (isArchived) {
    return busy ? "Unarchiving…" : "Unarchive";
  }
  return busy ? "Archiving…" : "Archive";
}

function ArchiveToggleButton({ projectId, status, onChanged }: Readonly<ArchiveToggleButtonProps>) {
  const { busy, run } = useAction();
  const [error, setError] = useState<string | null>(null);
  const isArchived = status === "archived";

  const handleClick = (): void => {
    setError(null);
    run(() => api.updateProject(projectId, { status: isArchived ? "active" : "archived" }))
      .then((updated) => {
        onChanged(updated);
      })
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  return (
    <div className="space-y-1">
      <Button
        variant="ghost"
        data-testid="project-archive-toggle"
        onClick={handleClick}
        disabled={busy}
      >
        {archiveToggleLabel(isArchived, busy)}
      </Button>
      {error !== null && (
        <p role="alert" data-testid="project-archive-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
    </div>
  );
}

function projectDeleteErrorMessage(err: unknown): string {
  const raw = errorMessageOf(err);
  if (raw.includes("project_has_workspaces")) {
    return `${raw} — archive the project instead, or remove/detach its workspaces first.`;
  }
  return raw;
}

interface DeleteProjectDialogProps {
  projectId: string;
  onClose: () => void;
  onDeleted: () => void;
}

function DeleteProjectDialog({ projectId, onClose, onDeleted }: Readonly<DeleteProjectDialogProps>) {
  const { busy, run } = useAction("rejection");
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = (): void => {
    setError(null);
    run(() => api.deleteProject(projectId))
      .then(() => {
        onDeleted();
      })
      .catch((err: unknown) => {
        setError(projectDeleteErrorMessage(err));
      });
  };

  return (
    <DialogFrame
      size="lg"
      ariaLabel="Delete project"
      testId="delete-project-dialog"
      busy={busy}
      onClose={onClose}
      onSubmit={handleConfirm}
    >
      <h3 className="text-base font-semibold text-text-primary">Delete project?</h3>
      <p className="text-sm text-text-primary">
        This permanently removes the project&apos;s record, document, and amendment history.
        This cannot be undone.
      </p>
      {error !== null && (
        <p role="alert" data-testid="delete-project-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" type="button" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="destroy"
          type="button"
          data-testid="delete-project-confirm"
          onClick={handleConfirm}
          disabled={busy}
        >
          {busy ? "Deleting…" : "Delete project"}
        </Button>
      </div>
    </DialogFrame>
  );
}

interface DeleteProjectActionProps {
  projectId: string;
  hasWorkspaces: boolean;
  onDeleted: () => void;
}

function DeleteProjectAction({
  projectId,
  hasWorkspaces,
  onDeleted,
}: Readonly<DeleteProjectActionProps>) {
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (hasWorkspaces) {
    return (
      <div className="space-y-1">
        <Button variant="destroy" data-testid="project-delete-button" disabled>
          Delete project
        </Button>
        <p className="text-xs text-text-muted" data-testid="project-delete-blocked-hint">
          This project still has workspaces attached. Archive it instead, or remove/detach its
          workspaces before deleting.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <Button
        variant="destroy"
        data-testid="project-delete-button"
        onClick={() => setDeleteOpen(true)}
      >
        Delete project
      </Button>
      {deleteOpen && (
        <DeleteProjectDialog
          projectId={projectId}
          onClose={() => setDeleteOpen(false)}
          onDeleted={onDeleted}
        />
      )}
    </div>
  );
}

interface DocAnchorProps {
  title: string;
  testId: string;
  content: string;
  emptyLabel: string;
}

function DocAnchor({ title, testId, content, emptyLabel }: Readonly<DocAnchorProps>) {
  if (content.trim() === "") {
    return (
      <div data-testid={testId} className="space-y-1">
        <SectionHeading level="h4">{title}</SectionHeading>
        <p className="text-sm text-text-muted" data-testid={`${testId}-empty`}>
          {emptyLabel}
        </p>
      </div>
    );
  }
  return (
    <MarkdownPanel
      title={title}
      ariaLabel={title}
      testId={testId}
      contentClassName="text-sm text-text-primary"
    >
      {content}
    </MarkdownPanel>
  );
}

function ProjectDocSection({ state }: Readonly<{ state: UseProjectDocResult }>) {
  if (state.status === "loading") {
    return (
      <p className="text-sm text-text-muted" data-testid="project-doc-loading">
        Loading doc…
      </p>
    );
  }

  if (state.status === "error") {
    return (
      <div role="alert" data-testid="project-doc-error" className="space-y-2">
        <p className="text-xs text-btn-destroy">{state.message}</p>
        <Button variant="ghost" onClick={state.refetch} data-testid="project-doc-retry">
          Retry
        </Button>
      </div>
    );
  }

  const { doc } = state;
  return (
    <div data-testid="project-doc" className="space-y-4">
      <DocAnchor
        title="Vision"
        testId="project-doc-vision"
        content={doc.vision}
        emptyLabel="No vision recorded yet."
      />
      <DocAnchor
        title="Scope / Anti-scope"
        testId="project-doc-scope"
        content={doc.scope}
        emptyLabel="No scope recorded yet."
      />
      <DocAnchor
        title="Decision Log"
        testId="project-doc-decision-log"
        content={doc.decision_log}
        emptyLabel="No decisions recorded yet."
      />
      <DocAnchor
        title="Discovered Facts"
        testId="project-doc-discovered-facts"
        content={doc.discovered_facts}
        emptyLabel="No discovered facts yet."
      />
    </div>
  );
}

interface ProposeAmendmentModalProps {
  projectId: string;
  initialVision: string;
  initialScope: string;
  onClose: () => void;
  onProposed: () => void;
}

function ProposeAmendmentModal({
  projectId,
  initialVision,
  initialScope,
  onClose,
  onProposed,
}: Readonly<ProposeAmendmentModalProps>) {
  const [vision, setVision] = useState(initialVision);
  const [scope, setScope] = useState(initialScope);
  const [decisionEntry, setDecisionEntry] = useState("");
  const { busy: submitting, run } = useAction();
  const [error, setError] = useState<string | null>(null);

  const canSubmit =
    vision.trim() !== "" && scope.trim() !== "" && decisionEntry.trim() !== "";

  const handleSubmit = (): void => {
    if (!canSubmit) return;
    setError(null);
    run(() =>
      api.proposeProjectAmendment(projectId, vision.trim(), scope.trim(), decisionEntry.trim()),
    )
      .then(onProposed)
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  return (
    <DialogFrame
      size="lg"
      ariaLabel="Propose vision/scope amendment"
      testId="propose-amendment-dialog"
      busy={submitting}
      onClose={onClose}
      onSubmit={handleSubmit}
    >
      <h2 className="text-base font-semibold text-text-primary">Propose amendment</h2>
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="amendment-vision">
          Vision
        </label>
        <Input
          as="textarea"
          id="amendment-vision"
          data-testid="amendment-vision"
          value={vision}
          onChange={(e) => setVision(e.target.value)}
          disabled={submitting}
          rows={3}
        />
      </div>
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="amendment-scope">
          Scope / Anti-scope
        </label>
        <Input
          as="textarea"
          id="amendment-scope"
          data-testid="amendment-scope"
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          disabled={submitting}
          rows={3}
        />
      </div>
      <div className="space-y-1">
        <label
          className="text-xs font-medium text-text-muted"
          htmlFor="amendment-decision-entry"
        >
          Decision log entry
        </label>
        <Input
          as="textarea"
          id="amendment-decision-entry"
          data-testid="amendment-decision-entry"
          value={decisionEntry}
          onChange={(e) => setDecisionEntry(e.target.value)}
          disabled={submitting}
          rows={2}
          placeholder="Why is this amendment being made?"
          placeholderStyle
        />
      </div>
      {error !== null && (
        <p role="alert" data-testid="propose-amendment-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" type="button" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button
          variant="create"
          type="button"
          data-testid="propose-amendment-submit"
          onClick={handleSubmit}
          disabled={submitting || !canSubmit}
        >
          {submitting ? "Proposing…" : "Propose amendment"}
        </Button>
      </div>
    </DialogFrame>
  );
}

interface AmendmentActionProps {
  projectId: string;
  doc: ProjectDoc;
}

function AmendmentAction({ projectId, doc }: Readonly<AmendmentActionProps>) {
  const [showModal, setShowModal] = useState(false);
  const [proposed, setProposed] = useState(false);

  return (
    <div className="space-y-2">
      {showModal && (
        <ProposeAmendmentModal
          projectId={projectId}
          initialVision={doc.vision}
          initialScope={doc.scope}
          onClose={() => setShowModal(false)}
          onProposed={() => {
            setShowModal(false);
            setProposed(true);
          }}
        />
      )}
      <Button
        variant="action"
        data-testid="propose-amendment-button"
        onClick={() => {
          setProposed(false);
          setShowModal(true);
        }}
      >
        Propose amendment
      </Button>
      {proposed && (
        <p className="text-sm text-text-muted" data-testid="propose-amendment-success">
          Amendment proposed.
        </p>
      )}
    </div>
  );
}

function RoadmapMergedPrs({
  prs,
  repo,
}: Readonly<{ prs: readonly number[]; repo: string | null }>) {
  if (prs.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-2" data-testid="roadmap-item-merged-prs">
      {prs.map((n) => (
        <li key={n}>
          <GithubRef
            repo={repo}
            kind="pull"
            prNumber={n}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-btn-action underline"
            testId={`roadmap-item-pr-link-${String(n)}`}
          >
            PR #{n}
          </GithubRef>
        </li>
      ))}
    </ul>
  );
}

interface RoadmapItemRowProps {
  projectId: string;
  item: RoadmapItemView;
  repo: string | null;
}

function RoadmapItemRow({ projectId, item, repo }: Readonly<RoadmapItemRowProps>) {
  return (
    <li className="space-y-1 py-2" data-testid={`roadmap-item-${item.id}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-text-primary">{item.title}</span>
        <RoadmapStatusBadge status={item.status} />
      </div>
      {item.text.trim() !== "" && (
        <MarkdownPanel
          title="Details"
          ariaLabel={`${item.title} details`}
          testId={`roadmap-item-text-${item.id}`}
          contentClassName="text-sm text-text-muted"
        >
          {item.text}
        </MarkdownPanel>
      )}
      <RoadmapMergedPrs prs={item.merged_prs!} repo={repo} />
      {item.status === "not_started" && (
        <Button
          variant="action"
          linkTo={`/build?project=${encodeURIComponent(projectId)}&roadmapItem=${encodeURIComponent(item.id)}`}
          data-testid={`roadmap-item-dispatch-${item.id}`}
        >
          Start work
        </Button>
      )}
    </li>
  );
}

interface RoadmapItemsSectionProps {
  projectId: string;
  items: readonly RoadmapItemView[];
  onImport: () => void;
  repo: string | null;
}

function RoadmapItemsSection({
  projectId,
  items,
  onImport,
  repo,
}: Readonly<RoadmapItemsSectionProps>) {
  if (items.length === 0) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-text-muted" data-testid="project-roadmap-empty">
          No roadmap imported yet.
        </p>
        <Button variant="create" data-testid="import-roadmap-open" onClick={onImport}>
          Import roadmap
        </Button>
      </div>
    );
  }
  const ordered = [...items].sort((a, b) => a.order - b.order);
  return (
    <ul className="divide-y divide-border" data-testid="project-roadmap-list">
      {ordered.map((item) => (
        <RoadmapItemRow key={item.id} projectId={projectId} item={item} repo={repo} />
      ))}
    </ul>
  );
}

interface RoadmapChangesActionProps {
  projectId: string;
  items: readonly RoadmapItemView[];
}

function RoadmapChangesAction({ projectId, items }: Readonly<RoadmapChangesActionProps>) {
  const [showModal, setShowModal] = useState(false);
  const [proposed, setProposed] = useState(false);

  if (items.length === 0) return null;

  return (
    <div className="space-y-2">
      {showModal && (
        <ProposeRoadmapChangesModal
          projectId={projectId}
          items={items}
          onClose={() => setShowModal(false)}
          onProposed={() => {
            setShowModal(false);
            setProposed(true);
          }}
        />
      )}
      <Button
        variant="ghost"
        data-testid="propose-roadmap-changes-open"
        onClick={() => {
          setProposed(false);
          setShowModal(true);
        }}
      >
        Propose changes
      </Button>
      {proposed && (
        <p className="text-sm text-text-muted" data-testid="propose-roadmap-changes-success">
          Change proposed.
        </p>
      )}
    </div>
  );
}

interface ImportRoadmapDialogProps {
  show: boolean;
  projectId: string;
  onClose: () => void;
  onImported: () => void;
}

function ImportRoadmapDialog({ show, projectId, onClose, onImported }: Readonly<ImportRoadmapDialogProps>) {
  if (!show) return null;
  return <ImportRoadmapModal projectId={projectId} onClose={onClose} onImported={onImported} />;
}

function ShippedSummary({ docState }: Readonly<{ docState: UseProjectDocResult }>) {
  if (docState.status !== "ready") return null;
  return (
    <MarkdownPanel
      title="Final summary"
      ariaLabel="Final summary"
      testId="project-complete-summary"
      contentClassName="text-sm text-text-primary"
    >
      {docState.doc.decision_log}
    </MarkdownPanel>
  );
}

interface CompleteActionProps {
  projectId: string;
  onCompleted: (updated: ProjectFields) => void;
}

function ReadyToCompleteButton({ projectId, onCompleted }: Readonly<CompleteActionProps>) {
  const { busy: submitting, run } = useAction();
  const [error, setError] = useState<string | null>(null);

  const handleClick = (): void => {
    setError(null);
    run(() => api.completeProject(projectId))
      .then(onCompleted)
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  return (
    <div className="space-y-2" data-testid="project-complete-ready">
      <Button
        variant="create"
        data-testid="project-complete-button"
        onClick={handleClick}
        disabled={submitting}
      >
        {submitting ? "Marking shipped…" : "Mark shipped"}
      </Button>
      {error !== null && (
        <p role="alert" data-testid="project-complete-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
    </div>
  );
}

function ForceCompleteDisclosure({ projectId, onCompleted }: Readonly<CompleteActionProps>) {
  const [reason, setReason] = useState("");
  const { busy: submitting, run } = useAction();
  const [error, setError] = useState<string | null>(null);
  const canSubmit = reason.trim() !== "";

  const handleForce = (): void => {
    setError(null);
    run(() => api.completeProject(projectId, { force: true, reason: reason.trim() }))
      .then(onCompleted)
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  return (
    <details data-testid="project-force-complete-disclosure">
      <summary className="cursor-pointer text-xs font-medium text-text-muted">
        Not every workspace is done — force ship anyway
      </summary>
      <div className="mt-2 space-y-2">
        <label className="text-xs font-medium text-text-muted" htmlFor="project-force-reason">
          Reason (required)
        </label>
        <Input
          as="textarea"
          id="project-force-reason"
          data-testid="project-force-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={submitting}
          rows={2}
        />
        {error !== null && (
          <p
            role="alert"
            data-testid="project-force-complete-error"
            className="text-sm text-btn-destroy"
          >
            {error}
          </p>
        )}
        <Button
          variant="destroy"
          data-testid="project-force-complete-submit"
          onClick={handleForce}
          disabled={submitting || !canSubmit}
        >
          {submitting ? "Marking shipped…" : "Force ship"}
        </Button>
      </div>
    </details>
  );
}

interface CompleteProjectSectionProps {
  projectId: string;
  project: ProjectDetailData;
  docState: UseProjectDocResult;
  onCompleted: (updated: ProjectFields) => void;
}

function CompleteProjectSection({
  projectId,
  project,
  docState,
  onCompleted,
}: Readonly<CompleteProjectSectionProps>) {
  if (project.status === "completed") {
    return (
      <div className="space-y-2" data-testid="project-complete-shipped">
        <p className="text-sm text-text-primary">This project has shipped.</p>
        <ShippedSummary docState={docState} />
      </div>
    );
  }
  if (project.ready_to_complete) {
    return <ReadyToCompleteButton projectId={projectId} onCompleted={onCompleted} />;
  }
  return <ForceCompleteDisclosure projectId={projectId} onCompleted={onCompleted} />;
}

function UnboundNoticeSlot({ state }: Readonly<{ state: unknown }>) {
  const reason = readConnectUnboundState(state);
  return reason === null ? null : <ConnectUnboundNotice reason={reason} />;
}

function projectRepo(project: ProjectDetailData): string | null {
  return project.engine_key ?? null;
}

interface ProjectRoadmapSectionProps {
  projectId: string;
  project: ProjectDetailData;
  repo: string | null;
  showImportRoadmap: boolean;
  onOpenImport: () => void;
  onCloseImport: () => void;
  onImported: () => void;
}

function ProjectRoadmapSection({
  projectId,
  project,
  repo,
  showImportRoadmap,
  onOpenImport,
  onCloseImport,
  onImported,
}: Readonly<ProjectRoadmapSectionProps>) {
  const roadmap = project.roadmap!;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text-primary">Roadmap</h3>
      {project.status === "active" && (
        <NextItemProposal
          projectId={projectId}
          autonomy={project.autonomy}
          roadmapDelivered={roadmap.filter((item) => item.status === "delivered").length}
          roadmapTotal={roadmap.length}
        />
      )}
      <RoadmapItemsSection
        projectId={projectId}
        items={roadmap}
        onImport={onOpenImport}
        repo={repo}
      />
      <RoadmapChangesAction projectId={projectId} items={roadmap} />
      <ImportRoadmapDialog
        show={showImportRoadmap}
        projectId={projectId}
        onClose={onCloseImport}
        onImported={onImported}
      />
    </section>
  );
}

function opsPanelEngineKey(
  projectEngineKey: string | null | undefined,
  defaultEngine: DefaultEngineState,
): string | null {
  if (projectEngineKey == null || !defaultEngine.settled) return null;
  return projectEngineKey === defaultEngine.key ? null : projectEngineKey;
}

const PROJECT_DETAIL_NAMESPACE = "project-detail";

function useProjectDetail(projectId: string | undefined) {
  const view = useDocQuery(PROJECT_DETAIL_NAMESPACE, projectId, (id) => api.getProject(id));
  const setProject = useSetDocData<ProjectDetailData>(PROJECT_DETAIL_NAMESPACE, projectId);
  const refetchProject = useRefetchDoc(PROJECT_DETAIL_NAMESPACE, projectId);
  const loadError = view.data === undefined && view.isError ? errorMessageOf(view.error) : null;
  return { project: view.data ?? null, loadError, setProject, refetchProject };
}

export default function Project() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [showImportRoadmap, setShowImportRoadmap] = useState(false);
  const docState = useProjectDoc(projectId);
  const defaultEngine = useDefaultEngineState();
  const { project, loadError, setProject, refetchProject } = useProjectDetail(projectId);

  if (loadError !== null) {
    return <ErrorBanner data-testid="project-detail-error">{loadError}</ErrorBanner>;
  }

  if (project === null || projectId === undefined) {
    return (
      <p className="text-sm text-text-muted" data-testid="project-detail-loading">
        Loading…
      </p>
    );
  }

  const handleAutonomyBudgetSaved = (updated: ProjectFields): void => {
    setProject((previous) => previous && { ...previous, ...updated });
  };

  const handleCompleted = (updated: ProjectFields): void => {
    setProject((previous) => previous && { ...previous, ...updated });
    if (docState.status !== "loading") docState.refetch();
  };

  const handleDeleted = (): void => {
    void navigate("/projects");
  };

  const handleRoadmapImported = (): void => {
    setShowImportRoadmap(false);
    refetchProject();
  };

  const repo = projectRepo(project);
  const workspaces = project.workspaces!;
  const opsEngineKey = opsPanelEngineKey(project.engine_key, defaultEngine);

  return (
    <div className="space-y-6" data-testid="project-detail">
      <UnboundNoticeSlot state={location.state as unknown} />
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold text-text-primary">{project.title}</h2>
          <ProjectStatusBadge status={project.status} />
        </div>
        <DocRefLink docRef={project.doc_ref} repo={repo} />
        <p className="text-xs text-text-muted" data-testid="project-dates">
          Created {formatDate(project.created_at)} · Updated {formatDate(project.updated_at)}
        </p>
      </div>

      <section className="flex flex-wrap items-start gap-3" data-testid="project-lifecycle-actions">
        {project.status !== "completed" && (
          <ArchiveToggleButton
            projectId={projectId}
            status={project.status}
            onChanged={handleAutonomyBudgetSaved}
          />
        )}
        <DeleteProjectAction
          projectId={projectId}
          hasWorkspaces={workspaces.length > 0}
          onDeleted={handleDeleted}
        />
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium text-text-primary">Autonomy &amp; budget</h3>
        <AutonomyBudgetEditor project={project} onSaved={handleAutonomyBudgetSaved} />
        <ProjectBurnView budget={project.budget} />
      </section>

      {opsEngineKey !== null && <EngineOpsPanel engineKey={opsEngineKey} />}

      <section className="space-y-3">
        <h3 className="text-sm font-medium text-text-primary">Project doc</h3>
        <ProjectDocSection state={docState} />
        {docState.status === "ready" && (
          <AmendmentAction projectId={projectId} doc={docState.doc} />
        )}
      </section>

      <ProjectRoadmapSection
        projectId={projectId}
        project={project}
        repo={repo}
        showImportRoadmap={showImportRoadmap}
        onOpenImport={() => setShowImportRoadmap(true)}
        onCloseImport={() => setShowImportRoadmap(false)}
        onImported={handleRoadmapImported}
      />

      <section className="space-y-2">
        <h3 className="text-sm font-medium text-text-primary">Conversations</h3>
        <ConversationsSection projectId={projectId} />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium text-text-primary">Workspaces</h3>
        <WorkspacesSection workspaces={workspaces} />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium text-text-primary">Pull requests</h3>
        <PullRequestsSection workspaces={workspaces} repo={repo} />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium text-text-primary">Ship</h3>
        <CompleteProjectSection
          projectId={projectId}
          project={project}
          docState={docState}
          onCompleted={handleCompleted}
        />
      </section>
    </div>
  );
}
