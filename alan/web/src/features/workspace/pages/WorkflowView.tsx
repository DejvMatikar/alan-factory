import { Link, useParams } from "react-router-dom";

import InvocationCard from "../components/InvocationCard";
import { useInstructionReadbacks } from "../hooks/useInstructionReadbacks";
import { useLiveWorkspace } from "../hooks/workflowLive";

export default function WorkflowView() {
  const { taskId, invId } = useParams<{ taskId: string; invId: string }>();
  const live = useLiveWorkspace(taskId);
  const { readbacks } = useInstructionReadbacks(taskId);

  if (!taskId || !invId) {
    return (
      <p className="text-sm text-text-muted" data-testid="missing-ids">
        Missing task or invocation id.
      </p>
    );
  }
  const invocation = live.invocations[invId];
  return (
    <div className="space-y-4">
      <Link
        to={`/workspaces/${encodeURIComponent(taskId)}`}
        className="text-xs text-text-muted hover:text-text-primary"
      >
        ← Back to workspace
      </Link>
      <h2 className="text-base font-medium" data-testid="workflow-page-title">
        {taskId} · {invId}
      </h2>
      {live.streamError !== null && (
        <div
          data-testid="stream-warning"
          className="rounded-md bg-amber-500/15 px-3 py-2 text-xs text-amber-300"
        >
          Live updates unavailable: {live.streamError}
        </div>
      )}
      <InvocationCard
        taskId={taskId}
        invId={invId}
        liveStreams={invocation?.liveStreams ?? {}}
        nodeUpdates={invocation?.nodeUpdates ?? []}
        instructionReadback={readbacks[invId]}
      />
    </div>
  );
}
