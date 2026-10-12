import type { WorkspaceStatus } from "../api";

export const STATUS_LABELS: Record<WorkspaceStatus, string> = {
  active: "active",
  awaiting_input: "awaiting input",
  completed: "completed",
  archived: "archived",
  escalated: "escalated",
  paused: "paused",
  failed: "failed",
  partial: "partial",
};

export const STATUS_STYLES: Record<WorkspaceStatus, string> = {
  active: "bg-status-active-tint/15 text-status-active-fg",
  awaiting_input: "bg-status-attention-tint/15 text-status-attention-fg",
  completed: "bg-status-success-tint/15 text-status-success-fg",
  archived: "bg-status-neutral-tint/15 text-status-neutral-fg",
  escalated: "bg-status-danger-tint/15 text-status-danger-fg",
  paused: "bg-status-paused-tint/15 text-status-paused-fg",
  failed: "bg-status-danger-tint/15 text-status-danger-fg",
  partial: "bg-status-warning-tint/15 text-status-warning-fg",
};

export const STATUS_GLYPHS: Partial<Record<WorkspaceStatus, string>> = {
  escalated: "↑",
  failed: "✕",
};

export default function StatusBadge({
  status,
}: Readonly<{ status: WorkspaceStatus }>) {
  const glyph = STATUS_GLYPHS[status];
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs ${STATUS_STYLES[status]}`}
      data-testid={`status-${status}`}
    >
      {STATUS_LABELS[status]}
      {glyph && (
        <span className="ml-1" aria-hidden="true">
          {glyph}
        </span>
      )}
    </span>
  );
}
