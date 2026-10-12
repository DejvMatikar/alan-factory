import { createElement, type ReactNode } from "react";

import { ApiError, type WorkspaceStatus } from "../../../api";
import { ArchiveIcon, StopIcon, UnarchiveIcon } from "../../../components/Button";

export type RowActionKind = "stop" | "archive" | "unarchive";

export interface RowAction {
  readonly kind: RowActionKind;
  readonly label: string;
  readonly busyLabel: string;
  readonly description: string;
  readonly icon: ReactNode;
}

const STOP: RowAction = {
  kind: "stop",
  label: "Stop",
  busyLabel: "Stopping…",
  description: "Stop this workspace's running job.",
  icon: createElement(StopIcon),
};

const ARCHIVE: RowAction = {
  kind: "archive",
  label: "Archive",
  busyLabel: "Archiving…",
  description: "Archive this workspace.",
  icon: createElement(ArchiveIcon),
};

const UNARCHIVE: RowAction = {
  kind: "unarchive",
  label: "Unarchive",
  busyLabel: "Unarchiving…",
  description: "Unarchive this workspace.",
  icon: createElement(UnarchiveIcon),
};

const ACTION_FOR_STATUS: Record<WorkspaceStatus, RowAction> = {
  active: STOP,
  archived: UNARCHIVE,
  awaiting_input: ARCHIVE,
  completed: ARCHIVE,
  escalated: ARCHIVE,
  paused: ARCHIVE,
  failed: ARCHIVE,
  partial: ARCHIVE,
};

export function rowActionFor(status: WorkspaceStatus): RowAction {
  return ACTION_FOR_STATUS[status];
}

export function rowActionAriaLabel(action: RowAction, busy: boolean, workspaceTitle: string): string {
  return `${busy ? action.busyLabel : action.label} — ${workspaceTitle}`;
}

export type RowActionErrorClassification = "benign" | "failed";

export function classifyRowActionError(err: unknown): RowActionErrorClassification {
  if (err instanceof ApiError && (err.status === 404 || err.status === 409)) return "benign";
  return "failed";
}
