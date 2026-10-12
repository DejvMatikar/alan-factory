import type React from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, type NodeStatus, type StreamEvent } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import DescribedIconButton from "../../../components/DescribedIconButton";
import { aggregateToolCounts, summarizeEvent } from "../../../components/streamSummary";
import Disclosure from "../../../ui/Disclosure";

export interface TaggedEvent {
  id: string;
  event: StreamEvent;
}

export interface StreamPanelProps {
  taskId: string;
  invId: string;
  role: string;
  liveEvents: readonly TaggedEvent[];
  nodeError?: string | null | undefined;
  nodeStatus?: NodeStatus;
}

function isInitiallyCollapsed(status: NodeStatus | undefined): boolean {
  return status !== "pending" && status !== "running";
}

function showEmptyMessage(
  all: readonly TaggedEvent[],
  loaded: TaggedEvent[] | null,
  collapsed: boolean,
): boolean {
  return all.length === 0 && loaded !== null && !collapsed;
}

function formatCountText(
  counts: { name: string; count: number }[],
  totalOther: number,
): string {
  const parts = counts.map(({ name, count }) => `${name} ×${count}`);
  if (totalOther > 0) parts.push(`${totalOther} other`);
  return parts.join(" · ");
}

function StatsToggle({
  collapsed,
  onToggle,
}: Readonly<{ collapsed: boolean; onToggle: () => void }>) {
  return (
    <DescribedIconButton
      glyph={collapsed ? "▸" : "▾"}
      label={collapsed ? "Expand events" : "Collapse events"}
      description={collapsed ? "Show this step's events" : "Hide this step's events"}
      testId="stream-stats-toggle"
      expanded={!collapsed}
      onClick={onToggle}
    />
  );
}

interface StatsBarProps {
  events: StreamEvent[];
  collapsed: boolean;
  nodeStatus: NodeStatus | undefined;
  onToggle: () => void;
}

function StatsBar({
  events,
  collapsed,
  nodeStatus,
  onToggle,
}: Readonly<StatsBarProps>): React.JSX.Element | null {
  if (events.length === 0) return null;
  const { currentAction, counts, totalOther } = aggregateToolCounts(events);
  const isRunning = nodeStatus === "running" || nodeStatus === "pending";
  const prefix = isRunning ? "Currently:" : "Last:";
  const countText = formatCountText(counts, totalOther);
  return (
    <footer
      data-testid="stream-stats-bar"
      className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-border bg-surface px-2 py-1 text-[10px] text-text-muted"
    >
      <StatsToggle collapsed={collapsed} onToggle={onToggle} />
      {currentAction && (
        <span data-testid="stats-current-action">
          {prefix} {currentAction}
        </span>
      )}
      {countText && (
        <span data-testid="stats-tool-counts">{countText}</span>
      )}
    </footer>
  );
}

function usePanelStream(
  taskId: string,
  invId: string,
  role: string,
  liveEvents: readonly TaggedEvent[],
): {
  all: readonly TaggedEvent[];
  loaded: TaggedEvent[] | null;
  error: string | null;
} {
  const [loaded, setLoaded] = useState<TaggedEvent[] | null>(null);
  const [liveOffset, setLiveOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const liveLengthAtMount = liveEvents.length;

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoaded(null);
    setError(null);
    setLiveOffset(0);
    api
      .getAgentStream(taskId, invId, role)
      .then((events) => {
        if (cancelled) return;
        setLoaded(
          events.map((event, i) => ({ id: `disk-${role}-${i}`, event })),
        );
        setLiveOffset(liveLengthAtMount);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errorMessageOf(err));
      });
    return () => {
      cancelled = true;
    };
    // liveLengthAtMount intentionally captured once at mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, invId, role]);

  const all = useMemo<readonly TaggedEvent[]>(() => {
    const live = liveEvents.slice(liveOffset);
    if (loaded === null) return live;
    return [...loaded, ...live];
  }, [loaded, liveEvents, liveOffset]);

  return { all, loaded, error };
}

export default function StreamPanel({
  taskId,
  invId,
  role,
  liveEvents,
  nodeError = null,
  nodeStatus,
}: Readonly<StreamPanelProps>) {
  const { all, loaded, error } = usePanelStream(taskId, invId, role, liveEvents);
  const [collapsed, setCollapsed] = useState(() =>
    isInitiallyCollapsed(nodeStatus),
  );
  const rawEvents = useMemo(() => all.map((t) => t.event), [all]);
  const toggle = useCallback(() => setCollapsed((prev) => !prev), []);

  return (
    <aside
      className="space-y-2 rounded-md border border-border bg-surface p-3"
      data-testid="stream-panel"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{role}</h3>
        {loaded === null && error === null && (
          <span className="text-[10px] text-text-muted">loading…</span>
        )}
      </div>
      {error !== null && (
        <p className="text-xs text-red-300" data-testid="stream-error">
          {error}
        </p>
      )}
      {!collapsed && (
        <ol className="max-h-96 space-y-1 overflow-y-auto text-[11px]" data-testid="stream-event-list">
          {all.map((item) => (
            <StreamRow key={item.id} event={item.event} />
          ))}
        </ol>
      )}
      {showEmptyMessage(all, loaded, collapsed) && (
        <p className="text-xs text-text-muted" data-testid="stream-empty">
          No events yet.
        </p>
      )}
      <StatsBar
        events={rawEvents}
        collapsed={collapsed}
        nodeStatus={nodeStatus}
        onToggle={toggle}
      />
      <NodeErrorFooter error={nodeError} />
    </aside>
  );
}

function NodeErrorFooter({
  error,
}: Readonly<{ error: string | null }>): React.JSX.Element | null {
  if (error === null || error === "") return null;
  return (
    <footer
      data-testid="stream-node-error"
      className="rounded-md bg-red-500/15 px-2 py-1 text-xs text-red-300"
    >
      <span className="font-medium">node failed:</span> {error}
    </footer>
  );
}

function StreamRow({ event }: Readonly<{ event: StreamEvent }>) {
  const summary = summarizeEvent(event);
  const type = typeof event.type === "string" ? event.type : "event";
  return (
    <li
      data-testid={`stream-row-${type}`}
      className="rounded border border-border bg-surface-elevated px-2 py-1 font-mono"
    >
      <Disclosure preset="row" defaultOpen={false} label={summary}>
        <pre className="overflow-x-auto whitespace-pre-wrap break-words text-text-muted">
          {JSON.stringify(event, null, 2)}
        </pre>
      </Disclosure>
    </li>
  );
}
