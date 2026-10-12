import type { ReactNode } from "react";

export type ChecklistRowStatus =
  | "pass"
  | "fail"
  | "unknown"
  | "no_forge"
  | "pending"
  | "not_started"
  | "waiting"
  | "unverifiable";

const STATUS_LABEL: Record<ChecklistRowStatus, string> = {
  pass: "pass",
  fail: "fail",
  unknown: "unknown",
  no_forge: "no forge configured",
  pending: "not yet checked",
  not_started: "not started",
  waiting: "waiting on you",
  unverifiable: "can't verify from here",
};

const STATUS_CLASSES: Record<ChecklistRowStatus, string> = {
  pass: "bg-btn-create/15 text-btn-create",
  fail: "bg-btn-destroy/15 text-btn-destroy",
  unknown: "bg-btn-reset/15 text-btn-reset",
  no_forge: "bg-btn-reset/15 text-btn-reset",
  pending: "bg-surface text-text-muted",
  not_started: "bg-surface text-text-muted",
  waiting: "bg-surface text-text-muted",
  unverifiable: "bg-surface text-text-muted",
};

export interface ChecklistRowProps {
  label: string;
  status: ChecklistRowStatus;
  detail?: string | undefined;
  remedy?: string | undefined;
  action?: ReactNode;
  testId: string;
}

// One row of the identify/credential/reachability(/proposal) checklist, in
// the forced order docs/architecture/connect_wizard.md names. Distinct from
// FindingRow.tsx (keyed on the backend's EnginePreflightFinding shape and its
// raw, W1.1-owned hues) — this is keyed on the wizard's own rail-shaped props
// and reuses the tokenized --color-btn-* status chip, minting no new token.
export default function ChecklistRow({
  label,
  status,
  detail,
  remedy,
  action,
  testId,
}: Readonly<ChecklistRowProps>) {
  const blocking = status === "fail";
  return (
    <div
      role={blocking ? "alert" : undefined}
      data-testid={testId}
      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface-elevated px-3 py-2"
    >
      <div className="space-y-0.5">
        <p className="text-sm font-medium text-text-primary" data-testid={`${testId}-label`}>
          {label}
        </p>
        {detail !== undefined && (
          <p className="text-xs text-text-muted" data-testid={`${testId}-detail`}>
            {detail}
          </p>
        )}
        {remedy !== undefined && (
          <p className="text-xs text-text-muted" data-testid={`${testId}-remedy`}>
            {remedy}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">
        <span
          data-testid={`${testId}-status`}
          className={`inline-block rounded px-2 py-0.5 text-xs ${STATUS_CLASSES[status]}`}
        >
          {STATUS_LABEL[status]}
        </span>
        {action}
      </div>
    </div>
  );
}
