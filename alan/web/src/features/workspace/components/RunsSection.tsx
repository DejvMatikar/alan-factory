import { useId, useState } from "react";

import type { DispatchInstructionsMap } from "../../../api";
import { handleTablistKeyDown } from "../../../components/tablistKeys";
import UnderlineTab from "../../../components/UnderlineTab";
import type { DocsState } from "../hooks/useInvocationDocs";
import { findHandoverFrom, useHandovers } from "../hooks/useHandovers";
import type { LiveInvocationState, LiveWorkspaceState } from "../hooks/workflowLive";
import InvocationGraph from "./InvocationGraph";
import RunRow from "./RunRow";

export interface RunsSectionProps {
  taskId: string;
  invocations: readonly string[];
  live: LiveWorkspaceState;
  docsState: DocsState;
  readbacks: DispatchInstructionsMap;
  repo: string | null;
}

type RunsView = "list" | "graph";

const EMPTY_LIVE_INVOCATION: LiveInvocationState = { nodeUpdates: [], liveStreams: {} };

function RunsViewTabs({
  uid,
  view,
  onChange,
}: Readonly<{ uid: string; view: RunsView; onChange: (view: RunsView) => void }>) {
  return (
    <div
      role="tablist"
      aria-label="Runs view"
      tabIndex={-1}
      className="flex gap-4 border-b border-border"
      onKeyDown={handleTablistKeyDown}
    >
      <UnderlineTab
        id={`runs-list-${uid}`}
        label="List"
        active={view === "list"}
        onClick={() => onChange("list")}
      />
      <UnderlineTab
        id={`runs-graph-${uid}`}
        label="Graph"
        active={view === "graph"}
        onClick={() => onChange("graph")}
      />
    </div>
  );
}

export default function RunsSection({
  taskId,
  invocations,
  live,
  docsState,
  readbacks,
  repo,
}: Readonly<RunsSectionProps>) {
  const uid = useId();
  const [view, setView] = useState<RunsView>("list");
  const { handovers, error: handoverError } = useHandovers(taskId, invocations);

  if (invocations.length === 0) {
    return (
      <InvocationGraph
        taskId={taskId}
        invocations={invocations}
        live={live}
        docsState={docsState}
        readbacks={readbacks}
        repo={repo}
      />
    );
  }

  return (
    <div className="space-y-3" data-testid="runs-section">
      <RunsViewTabs uid={uid} view={view} onChange={setView} />
      <div
        role="tabpanel"
        id={`tabpanel-runs-${uid}-${view}`}
        aria-labelledby={`tab-runs-${view}-${uid}`}
      >
        {view === "graph" ? (
          <InvocationGraph
            taskId={taskId}
            invocations={invocations}
            live={live}
            docsState={docsState}
            readbacks={readbacks}
            repo={repo}
          />
        ) : (
          <>
            {handoverError !== null && (
              <p className="text-xs text-red-400" data-testid="handover-load-error">
                handover load failed
              </p>
            )}
            <ul className="space-y-2" data-testid="runs-list">
              {invocations.map((invId) => (
                <RunRow
                  key={invId}
                  taskId={taskId}
                  invId={invId}
                  doc={docsState.docs.get(invId) ?? null}
                  loadError={docsState.errors.get(invId) ?? null}
                  liveInvocation={live.invocations[invId] ?? EMPTY_LIVE_INVOCATION}
                  readback={readbacks[invId]}
                  handover={findHandoverFrom(handovers, invId)}
                  repo={repo}
                />
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
