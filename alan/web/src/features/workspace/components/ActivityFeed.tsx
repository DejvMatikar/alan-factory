import { formatDate } from "../../../lib/format";
import type { Severity, WorkspaceNotice } from "../hooks/workspaceNotices";

const SEVERITY_STYLES: Record<Severity, string> = {
  info: "text-text-muted",
  warn: "text-amber-300",
  error: "text-red-300",
};

export default function ActivityFeed({
  notices,
}: Readonly<{ notices: readonly WorkspaceNotice[] }>) {
  if (notices.length === 0) return null;
  return (
    <ul data-testid="activity-feed" className="max-h-40 overflow-y-auto space-y-0.5">
      {notices.map((n) => (
        <li
          key={n.id}
          data-testid={`activity-entry-${n.id}`}
          className={`flex gap-2 text-xs ${SEVERITY_STYLES[n.severity]}`}
        >
          <time dateTime={n.timestamp} className="shrink-0 tabular-nums">
            {formatDate(n.timestamp)}
          </time>
          <span>{n.text}</span>
        </li>
      ))}
    </ul>
  );
}
