import type { Assumption, AssumptionStatus } from "../../../api";
import ClauseList from "./ClauseList";

interface AssumptionsListProps {
  assumptions: Assumption[];
}

const STATUS_LABELS: Record<Exclude<AssumptionStatus, "open">, string> = {
  accepted: "accepted",
  challenged: "challenged",
};

const STATUS_BADGE_CLASSES: Record<Exclude<AssumptionStatus, "open">, string> = {
  accepted: "bg-status-success-tint/15 text-status-success-fg",
  challenged: "bg-status-danger-tint/15 text-status-danger-fg",
};

function assumptionMarker(status: AssumptionStatus) {
  if (status === "open") return null;
  return (
    <span
      className={`inline-block shrink-0 rounded px-2 py-0.5 text-xs uppercase ${STATUS_BADGE_CLASSES[status]}`}
      aria-label={`status: ${STATUS_LABELS[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

export default function AssumptionsList({
  assumptions,
}: Readonly<AssumptionsListProps>) {
  return (
    <ClauseList
      items={assumptions}
      testId="assumptions-checklist"
      itemTestId={(a) => `assumption-${a.id}`}
      renderMarker={(a) => assumptionMarker(a.status)}
    />
  );
}
