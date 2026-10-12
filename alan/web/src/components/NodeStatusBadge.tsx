import type { NodeStatus } from "../api";

const NODE_STATUS_STYLES: Record<NodeStatus, string> = {
  pending: "bg-slate-500/15 text-slate-300",
  running: "bg-blue-500/15 text-blue-300",
  success: "bg-green-500/15 text-green-300",
  failed: "bg-red-500/15 text-red-300",
  interrupted: "bg-amber-500/15 text-amber-300",
};

export default function NodeStatusBadge({
  status,
}: Readonly<{ status: NodeStatus }>) {
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs ${NODE_STATUS_STYLES[status]}`}
      data-testid={`node-status-${status}`}
    >
      {status}
    </span>
  );
}
