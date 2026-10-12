import type { WorkspaceDetail } from "../../../api";
import { ArtifactsSection } from "./handoverSections";

export default function Deliverables({
  ws,
  taskId,
}: Readonly<{ ws: WorkspaceDetail; taskId: string }>) {
  const handover = ws.latest_handover;
  const artifacts = handover?.artifacts_produced ?? [];
  if (artifacts.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">Deliverables</h3>
      <ArtifactsSection
        artifacts={artifacts}
        prNumber={handover?.pr_number ?? null}
        taskId={taskId}
        repo={ws.engine_key ?? null}
        showHeading={false}
      />
    </section>
  );
}
