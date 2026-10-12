import type { RoadmapDeliveryStatus } from "../../../api";

const STATUS_LABELS: Record<RoadmapDeliveryStatus, string> = {
  not_started: "not started",
  in_flight: "in flight",
  delivered: "delivered",
};

const STATUS_STYLES: Record<RoadmapDeliveryStatus, string> = {
  not_started: "bg-zinc-500/15 text-zinc-300",
  in_flight: "bg-blue-500/15 text-blue-300",
  delivered: "bg-green-500/15 text-green-300",
};

export default function StatusBadge({
  status,
}: Readonly<{ status: RoadmapDeliveryStatus }>) {
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs ${STATUS_STYLES[status]}`}
      data-testid={`roadmap-status-${status}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}
