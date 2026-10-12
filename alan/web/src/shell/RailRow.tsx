import { useCallback } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import Button, { TrashIcon } from "../components/Button";
import type { DraftRow } from "../components/ChatDraftRows";
import { formatDate } from "../lib/format";
import { useConfirmedDelete } from "../hooks/useConfirmedDelete";
import { useConsultantLabel } from "../hooks/useConsultants";
import {
  ariaCurrentSelection,
  selectionRowSurfaceClass,
  selectionTextClass,
  UNSELECTED_PREVIEW_TEXT_CLASS,
  SELECTION_FOCUS_CLASS,
  SELECTION_HOVER_CLASS,
} from "./selection";

export function railRowPath(engineKey: string, row: DraftRow): string {
  const suffix =
    row.family === "build" ? "build" : `explore/${encodeURIComponent(row.consultantKey ?? "")}`;
  return `/repos/${engineKey}/${suffix}?s=${encodeURIComponent(row.sessionId)}`;
}

function stateLabel(row: DraftRow): string | null {
  if (row.inFlight) return "In progress";
  if (row.summaryReady) return "Summary ready";
  return null;
}

function resolveDeleteCall(family: DraftRow["family"], sessionId: string): Promise<void> {
  return family === "build"
    ? api.deleteBuildChatSession(sessionId)
    : api.deleteExploreChatSession(sessionId);
}

export interface RailRowProps {
  readonly row: DraftRow;
  readonly engineKey: string;
  readonly active: boolean;
  readonly onDeleted: (sessionId: string, wasActive: boolean) => void;
}

export default function RailRow({
  row,
  engineKey,
  active,
  onDeleted,
}: Readonly<RailRowProps>) {
  const deleteCall = useCallback(
    () => resolveDeleteCall(row.family, row.sessionId),
    [row.family, row.sessionId],
  );
  const onRowDeleted = useCallback(
    () => onDeleted(row.sessionId, active),
    [onDeleted, row.sessionId, active],
  );
  const { requestDelete, deleting, deleteError, modal } = useConfirmedDelete(deleteCall, onRowDeleted);
  const kind = useConsultantLabel(row.consultantKey) ?? row.fineType;

  const state = stateLabel(row);
  const preview = row.lastMessagePreview.length > 0 ? row.lastMessagePreview : "(no messages yet)";

  return (
    <li
      className={`relative ${SELECTION_HOVER_CLASS} ${selectionRowSurfaceClass(active)}`}
      data-testid={`rail-row-${row.sessionId}`}
    >
      <Link
        to={railRowPath(engineKey, row)}
        aria-current={ariaCurrentSelection(active)}
        className={`block min-w-0 py-2 pr-12 ${SELECTION_FOCUS_CLASS}`}
        data-testid={`rail-row-link-${row.sessionId}`}
      >
        <p className={`flex flex-wrap items-center gap-1.5 text-xs ${selectionTextClass(active)}`}>
          <span data-testid={`rail-row-date-${row.sessionId}`}>{formatDate(row.createdAt)}</span>
          <span aria-hidden="true">·</span>
          <span data-testid={`rail-row-kind-${row.sessionId}`}>{kind}</span>
          {state !== null && (
            <>
              <span aria-hidden="true">·</span>
              <span data-testid={`rail-row-state-${row.sessionId}`}>{state}</span>
            </>
          )}
        </p>
        <p
          className={`mt-0.5 line-clamp-2 text-sm ${UNSELECTED_PREVIEW_TEXT_CLASS}`}
          data-testid={`rail-row-description-${row.sessionId}`}
        >
          {preview}
        </p>
      </Link>
      <Button
        variant="destroy"
        iconOnly
        onClick={requestDelete}
        disabled={deleting}
        aria-label={`Delete ${kind} chat`}
        data-testid={`rail-row-delete-${row.sessionId}`}
        className="absolute right-0 top-1/2 shrink-0 -translate-y-1/2"
      >
        <TrashIcon />
      </Button>
      {deleteError !== null && (
        <p className="pr-12 pb-1 text-xs text-red-300" role="alert">
          {deleteError}
        </p>
      )}
      {modal}
    </li>
  );
}
