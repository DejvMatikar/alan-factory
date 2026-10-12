import { useCallback, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import Button from "../components/Button";
import HoverReveal from "../components/HoverReveal";
import ShortcutHint from "../components/ShortcutHint";
import { accentVars, type ResolvedAccent } from "../lib/repoAccent";
import { useShellExplore } from "./ShellExploreProvider";
import RailRow from "./RailRow";
import { RAIL_MAX_H_CLASS } from "./stickyOffsets";
import { useRepoConversations } from "./useRepoConversations";

export interface ConversationRailProps {
  readonly engineKey: string;
  readonly resolvedAccent: ResolvedAccent | null;
  readonly onCollapse?: () => void;
}

function RailHeading() {
  return (
    <p className="text-sm font-semibold text-text-primary" data-testid="rail-heading">
      Conversations
    </p>
  );
}

interface RailCollapseControlProps {
  readonly onCollapse: (() => void) | undefined;
}

function RailCollapseControl({ onCollapse }: Readonly<RailCollapseControlProps>) {
  if (!onCollapse) return null;
  return (
    <button
      type="button"
      onClick={onCollapse}
      aria-label="Collapse the conversations rail"
      aria-expanded={true}
      data-testid="rail-collapse"
      className="min-h-11 self-start rounded px-2 text-xs text-text-muted hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
    >
      Collapse «
    </button>
  );
}

interface StartControlsProps {
  readonly engineKey: string;
}

function StartControls({ engineKey }: Readonly<StartControlsProps>) {
  const navigate = useNavigate();
  const { openExplore } = useShellExplore();

  const handleBuild = useCallback(() => {
    Promise.resolve(
      navigate(`/repos/${engineKey}/build?engine=${encodeURIComponent(engineKey)}`),
    ).catch(
      /* v8 ignore next */
      () => undefined,
    );
  }, [navigate, engineKey]);

  const handleExplore = useCallback(() => {
    openExplore(engineKey);
  }, [openExplore, engineKey]);

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid={`rail-start-controls-${engineKey}`}>
      <HoverReveal
        id={`rail-start-build-${engineKey}-description`}
        description="Start a new build conversation to scope and dispatch work in this repository."
      >
        <Button variant="action" data-testid={`rail-start-build-${engineKey}`} onClick={handleBuild}>
          Build <ShortcutHint glyph="⌃⇧X" spoken="Control Shift X" />
        </Button>
      </HoverReveal>
      <HoverReveal
        id={`rail-start-explore-${engineKey}-description`}
        description="Open a free-form conversation to research or brainstorm about this repository."
      >
        <Button variant="action" data-testid={`rail-start-explore-${engineKey}`} onClick={handleExplore}>
          Explore <ShortcutHint glyph="⌃⇧E" spoken="Control Shift E" />
        </Button>
      </HoverReveal>
    </div>
  );
}

export default function ConversationRail({
  engineKey,
  resolvedAccent,
  onCollapse,
}: Readonly<ConversationRailProps>) {
  const { rows, loadError, removeRow } = useRepoConversations(engineKey);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [announcement, setAnnouncement] = useState<string | null>(null);
  const activeSessionId = searchParams.get("s");

  const handleDeleted = useCallback(
    (sessionId: string, wasActive: boolean) => {
      removeRow(sessionId);
      if (!wasActive) return;
      setAnnouncement("Conversation deleted — showing the repository dashboard.");
      Promise.resolve(
        navigate(`/repos/${engineKey}`, { replace: true }),
      ).catch(
        /* v8 ignore next */
        () => undefined,
      );
    },
    [removeRow, navigate, engineKey],
  );

  return (
    <div
      style={accentVars(resolvedAccent)}
      className={`flex ${RAIL_MAX_H_CLASS} min-h-0 flex-col gap-3`}
      data-testid={`conversation-rail-${engineKey}`}
    >
      <p
        aria-live="polite"
        role="status"
        className="sr-only"
        data-testid="conversation-rail-announcement"
      >
        {announcement ?? ""}
      </p>
      <RailHeading />
      <StartControls engineKey={engineKey} />
      <div
        className="min-h-0 flex-1 overflow-y-auto"
        data-testid="conversation-rail-scroll"
      >
        {loadError !== null && (
          <p role="alert" className="p-3 text-xs text-btn-destroy" data-testid="conversation-rail-error">
            {loadError}
          </p>
        )}
        {loadError === null && rows !== null && rows.length === 0 && (
          <p className="p-3 text-sm text-text-muted" data-testid="conversation-rail-empty">
            No conversations yet.
          </p>
        )}
        {rows !== null && rows.length > 0 && (
          <ul>
            {rows.map((row) => (
              <RailRow
                key={row.sessionId}
                row={row}
                engineKey={engineKey}
                active={row.sessionId === activeSessionId}
                onDeleted={handleDeleted}
              />
            ))}
          </ul>
        )}
      </div>
      <RailCollapseControl onCollapse={onCollapse} />
    </div>
  );
}
