import { formatAutoArchiveCountdown } from "../../../lib/format";

export interface AutoArchiveBadgeProps {
  readonly taskId: string;
  readonly updatedAt: string;
  readonly days: number;
}

function explainer(days: number): string {
  return `This workspace has been idle and may be automatically archived roughly ${String(days)} days after its last update. The countdown is approximate.`;
}

export default function AutoArchiveBadge({ taskId, updatedAt, days }: Readonly<AutoArchiveBadgeProps>) {
  const countdown = formatAutoArchiveCountdown(updatedAt, days);
  if (countdown === null) return null;
  const explainerId = `auto-archive-explainer-${taskId}`;
  return (
    <span
      data-testid={`auto-archive-badge-${taskId}`}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- keyboard-focusable to expose the aria-describedby explainer to screen readers; not a control
      tabIndex={0}
      title={explainer(days)}
      aria-describedby={explainerId}
      className="inline-block rounded px-2 py-0.5 text-xs bg-status-warning-tint/15 text-status-warning-fg focus:outline-none focus:ring-2 focus:ring-text-primary/60"
    >
      {countdown}
      <span id={explainerId} className="sr-only">
        {explainer(days)}
      </span>
    </span>
  );
}
