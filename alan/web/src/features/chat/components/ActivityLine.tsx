import { useId, useState } from "react";

import DescribedIconButton from "../../../components/DescribedIconButton";

export type ActivityLineVariant = "flow" | "overlay";

interface ActivityLineProps {
  entries: readonly string[];
  thinking: boolean;
  label?: string;
  variant?: ActivityLineVariant;
}

const DEFAULT_LABEL = "Alan's activity";

function CurrentAndThinking({
  last,
  thinking,
}: Readonly<{ last: string | undefined; thinking: boolean }>) {
  return (
    <div className="min-w-0 flex-1">
      {last !== undefined && (
        <span className="block truncate" data-testid="activity-line-current">
          {last}
        </span>
      )}
      {thinking && (
        <span aria-live="polite" data-testid="thinking">
          Alan is thinking…
        </span>
      )}
    </div>
  );
}

const FLOW_LIST_CLASS =
  "mt-1 w-full space-y-0.5 rounded-md border border-border bg-surface-elevated p-2";
const OVERLAY_LIST_CLASS =
  "absolute left-0 top-full z-10 mt-1 max-h-40 w-full space-y-0.5 overflow-y-auto rounded-md border border-border bg-surface-elevated p-2";
const FLOW_WRAPPER_CLASS = "mb-2 mr-auto w-full text-xs text-text-muted";
const OVERLAY_WRAPPER_CLASS =
  "relative mb-2 mr-auto flex max-w-[min(80%,65ch)] items-center gap-2 text-xs text-text-muted";

function ExpandedList({
  entries,
  listId,
  variant,
}: Readonly<{ entries: readonly string[]; listId: string; variant: ActivityLineVariant }>) {
  const overlay = variant === "overlay";
  const itemClassName = overlay ? "truncate" : "break-words";
  return (
    <ul
      id={listId}
      className={overlay ? OVERLAY_LIST_CLASS : FLOW_LIST_CLASS}
      data-testid="activity-line-list"
    >
      {entries.map((entry, idx) => (
        <li key={`${String(idx)}-${entry}`} className={itemClassName}>
          {entry}
        </li>
      ))}
    </ul>
  );
}

interface ToggleProps {
  expanded: boolean;
  listId: string;
  label: string;
  onToggle: () => void;
}

function Toggle({ expanded, listId, label, onToggle }: Readonly<ToggleProps>) {
  return (
    <DescribedIconButton
      glyph={expanded ? "▾" : "▸"}
      label={expanded ? `Hide ${label}` : `Show ${label}`}
      description={expanded ? "Collapse to the latest entry" : "See every recent entry"}
      testId="activity-line-toggle"
      expanded={expanded}
      controls={expanded ? listId : undefined}
      onClick={onToggle}
      className="border-0"
    />
  );
}

interface BodyProps {
  entries: readonly string[];
  thinking: boolean;
  expanded: boolean;
  listId: string;
  label: string;
  onToggle: () => void;
}

function OverlayBody({ entries, thinking, expanded, listId, label, onToggle }: Readonly<BodyProps>) {
  return (
    <div className={OVERLAY_WRAPPER_CLASS} data-testid="activity-line">
      <Toggle expanded={expanded} listId={listId} label={label} onToggle={onToggle} />
      <CurrentAndThinking last={entries.at(-1)} thinking={thinking} />
      {expanded && <ExpandedList entries={entries} listId={listId} variant="overlay" />}
    </div>
  );
}

function FlowBody({ entries, thinking, expanded, listId, label, onToggle }: Readonly<BodyProps>) {
  return (
    <div className={FLOW_WRAPPER_CLASS} data-testid="activity-line">
      <div className="flex items-center gap-2">
        <Toggle expanded={expanded} listId={listId} label={label} onToggle={onToggle} />
        <CurrentAndThinking last={entries.at(-1)} thinking={thinking} />
      </div>
      {expanded && <ExpandedList entries={entries} listId={listId} variant="flow" />}
    </div>
  );
}

export default function ActivityLine({
  entries,
  thinking,
  label = DEFAULT_LABEL,
  variant = "flow",
}: Readonly<ActivityLineProps>) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  if (entries.length === 0 && !thinking) return null;

  const bodyProps: BodyProps = {
    entries,
    thinking,
    expanded,
    listId,
    label,
    onToggle: () => setExpanded((v) => !v),
  };
  return variant === "overlay" ? <OverlayBody {...bodyProps} /> : <FlowBody {...bodyProps} />;
}
