import { useCallback } from "react";
import { Link } from "react-router-dom";

import {
  api,
  type BuildChatSessionSummary,
  type ExploreChatSessionSummary,
} from "../api";
import { useDocQuery, useRefetchDoc, useSetDocData } from "../api/queries";
import { errorMessageOf } from "../lib/errorMessage";
import { formatDate } from "../lib/format";
import { useConfirmedDelete } from "../hooks/useConfirmedDelete";
import { useConsultantLabel } from "../hooks/useConsultants";
import Button, { TrashIcon } from "./Button";
import EngineBadge from "./EngineBadge";

export type DraftFamily = "build" | "explore";

export interface DraftRow {
  sessionId: string;
  createdAt: string;
  lastMessagePreview: string;
  inFlight: boolean;
  summaryReady: boolean;
  family: DraftFamily;
  fineType: string;
  consultantKey: string | null;
  engineKey: string | null;
}

export function toRows(
  builds: BuildChatSessionSummary[],
  explores: ExploreChatSessionSummary[],
): DraftRow[] {
  const rows: DraftRow[] = [];
  for (const s of builds) {
    rows.push({
      sessionId: s.session_id,
      createdAt: s.created_at,
      lastMessagePreview: s.last_message_preview,
      inFlight: s.in_flight,
      summaryReady: s.summary_ready,
      family: "build",
      fineType: "Build",
      consultantKey: null,
      engineKey: s.engine_key ?? null,
    });
  }
  for (const s of explores) {
    rows.push({
      sessionId: s.session_id,
      createdAt: s.created_at,
      lastMessagePreview: s.last_message_preview,
      inFlight: s.in_flight,
      summaryReady: s.summary_ready,
      family: "explore",
      fineType: s.consultant_key,
      consultantKey: s.consultant_key,
      engineKey: s.engine_key ?? null,
    });
  }
  rows.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
  return rows;
}

export interface UseDraftRowsResult {
  rows: DraftRow[] | null;
  loadError: string | null;
  removeRow: (sessionId: string) => void;
}

export interface DraftScope {
  projectId?: string | undefined;
  engineKey?: string | undefined;
}

const DRAFTS_NAMESPACE = "chat-drafts";

function draftScopeKeyOf({ projectId, engineKey }: DraftScope): string {
  if (projectId !== undefined) return `project:${projectId}`;
  if (engineKey !== undefined) return `engine:${engineKey}`;
  return "all";
}

async function fetchDraftRows({ projectId, engineKey }: DraftScope): Promise<DraftRow[]> {
  const [buildResult, exploreResult] = await Promise.allSettled([
    api.listBuildChatSessions(projectId, engineKey),
    api.listExploreChatSessions(projectId, engineKey),
  ]);
  if (buildResult.status === "rejected" && exploreResult.status === "rejected") {
    throw buildResult.reason;
  }
  const builds = buildResult.status === "fulfilled" ? buildResult.value : [];
  const explores = exploreResult.status === "fulfilled" ? exploreResult.value : [];
  return toRows(builds, explores);
}

export function useScopedDraftRows(scope: DraftScope, pollMs?: number): UseDraftRowsResult {
  const key = draftScopeKeyOf(scope);
  const view = useDocQuery(DRAFTS_NAMESPACE, key, () => fetchDraftRows(scope), { pollMs });
  const setRows = useSetDocData<DraftRow[]>(DRAFTS_NAMESPACE, key);

  const removeRow = useCallback(
    (sessionId: string) => {
      setRows((previous) => previous?.filter((r) => r.sessionId !== sessionId));
    },
    [setRows],
  );

  return {
    rows: view.data ?? null,
    loadError: view.isError ? errorMessageOf(view.error) : null,
    removeRow,
  };
}

export function useRefetchDraftRows(scope: DraftScope): () => void {
  return useRefetchDoc(DRAFTS_NAMESPACE, draftScopeKeyOf(scope));
}

export function useDraftRows(projectId?: string): UseDraftRowsResult {
  return useScopedDraftRows({ projectId });
}

function draftLink(row: DraftRow): string {
  if (row.family === "build") return `/build?s=${encodeURIComponent(row.sessionId)}`;
  /* v8 ignore next */
  return `/explore/${encodeURIComponent(row.consultantKey ?? "")}?s=${encodeURIComponent(row.sessionId)}`;
}

export const IN_PROGRESS_BADGE_CLASS =
  "rounded-full px-2 py-0.5 text-xs font-medium bg-amber-500/15 text-amber-300";

function familyColor(family: DraftFamily): string {
  if (family === "build") return "bg-violet-500/15 text-violet-300";
  return "bg-teal-500/15 text-teal-300";
}

function statusBadge(row: DraftRow): { label: string; cls: string } | null {
  if (row.inFlight) return { label: "In progress", cls: IN_PROGRESS_BADGE_CLASS };
  if (row.summaryReady) {
    return {
      label: "Summary ready",
      cls: "rounded-full px-2 py-0.5 text-xs font-medium bg-emerald-500/15 text-emerald-300",
    };
  }
  return null;
}

function resolveDeleteCall(family: DraftFamily, sessionId: string) {
  if (family === "build") return api.deleteBuildChatSession(sessionId);
  return api.deleteExploreChatSession(sessionId);
}

interface DraftRowProps {
  row: DraftRow;
  onDeleted: (sessionId: string) => void;
  showEngineMarker?: boolean;
}

export function DraftRowItem({
  row,
  onDeleted,
  showEngineMarker = true,
}: Readonly<DraftRowProps>) {
  const deleteCall = useCallback(
    () => resolveDeleteCall(row.family, row.sessionId),
    [row.family, row.sessionId],
  );
  const onRowDeleted = useCallback(
    () => onDeleted(row.sessionId),
    [onDeleted, row.sessionId],
  );
  const { requestDelete, deleting, deleteError, modal } = useConfirmedDelete(
    deleteCall,
    onRowDeleted,
  );
  const kind = useConsultantLabel(row.consultantKey) ?? row.fineType;

  const preview =
    row.lastMessagePreview.length > 0 ? row.lastMessagePreview : "(no messages yet)";
  const badge = statusBadge(row);

  return (
    <li
      className="border-b border-border/60 last:border-b-0"
      data-testid={`draft-row-${row.sessionId}`}
    >
      <div className="flex items-start">
        <Link
          to={draftLink(row)}
          className="flex min-w-0 flex-1 items-start justify-between gap-3 px-3 py-2 hover:bg-surface-elevated"
          data-testid={`resume-draft-${row.sessionId}`}
        >
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-text-primary">{preview}</p>
            <p className="flex items-center gap-1.5 text-xs text-text-muted">
              <span data-testid={`draft-timestamp-${row.sessionId}`}>
                {formatDate(row.createdAt)}
              </span>
              {showEngineMarker && (
                <EngineBadge
                  engineKey={row.engineKey}
                  testIdSuffix={`draft-repo-${row.sessionId}`}
                  showDefault
                />
              )}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${familyColor(row.family)}`}
              data-testid={`fine-type-${row.sessionId}`}
            >
              {kind}
            </span>
            {badge !== null && (
              <span
                className={badge.cls}
                data-testid={`status-badge-${row.sessionId}`}
              >
                {badge.label}
              </span>
            )}
          </div>
        </Link>
        <Button
          variant="destroy"
          iconOnly
          onClick={requestDelete}
          disabled={deleting}
          aria-label={`Delete ${kind} chat`}
          data-testid={`delete-draft-${row.sessionId}`}
          className="mr-2 shrink-0"
        >
          <TrashIcon />
        </Button>
      </div>
      {deleteError !== null && (
        <p className="px-3 pb-1 text-xs text-red-300" role="alert">
          {deleteError}
        </p>
      )}
      {modal}
    </li>
  );
}
