import { useHealthContext } from "../api/HealthProvider";
import type { DispatchReadinessIssue } from "../api/HealthProvider";

const HELD_LABEL = "Dispatch held";
const UNVERIFIED_LABEL = "Dispatch readiness unverified";
const WARNING_LABEL = "Dispatch readiness warning";

function IssueItem({ issue }: Readonly<{ issue: DispatchReadinessIssue }>) {
  return (
    <li data-testid="dispatch-readiness-issue">
      <span className="font-medium">{issue.engine}</span>
      {` — ${issue.requirement}: ${issue.message}. Fix: ${issue.fix}`}
    </li>
  );
}

export default function DispatchReadinessBanner() {
  const { dispatchReadiness } = useHealthContext();
  const issues = dispatchReadiness ?? [];
  if (issues.length === 0) return null;
  const held = issues.some((issue) => issue.state === "not_ready");
  const unverified = issues.some((issue) => issue.state === "unknown");
  let heading = WARNING_LABEL;
  if (held) {
    heading = HELD_LABEL;
  } else if (unverified) {
    heading = UNVERIFIED_LABEL;
  }
  const headingClassName =
    held || unverified ? "font-medium text-btn-reset" : "font-medium text-status-warning-fg";
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="dispatch-readiness-banner"
      className="border-b border-border bg-surface-elevated px-6 py-2 text-sm text-text-primary"
    >
      <span className={headingClassName}>{heading}</span>
      <ul className="mt-1 space-y-0.5">
        {issues.map((issue) => (
          <IssueItem key={`${issue.engine}/${issue.requirement}/${issue.state}`} issue={issue} />
        ))}
      </ul>
    </div>
  );
}
