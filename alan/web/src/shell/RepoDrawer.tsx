import { Link } from "react-router-dom";

import type { EngineSummary } from "../api";
import Button from "../components/Button";
import { useModalLifecycle } from "../ui/useModalLifecycle";
import { switcherEntries } from "../lib/fold";
import { accentEdgeClass, accentTextClass, accentVars, resolveAccents } from "../lib/repoAccent";
import { useEngines } from "../hooks/useEngines";
import ConversationRail from "./ConversationRail";
import {
  ariaCurrentSelection,
  SELECTION_INTERACTIVE_CLASS,
  SELECTION_TEXT_UNSELECTED_CLASS,
} from "./selection";

const REPO_LINK_CLASS = `min-h-11 rounded-md px-2 py-1.5 text-sm font-medium border-l-4 ${SELECTION_INTERACTIVE_CLASS}`;

interface DrawerRepoListProps {
  readonly engineKey: string;
  readonly engines: readonly EngineSummary[];
  readonly allEngines: readonly EngineSummary[];
  readonly defaultKey: string | null;
}

function DrawerRepoList({
  engineKey,
  engines,
  allEngines,
  defaultKey,
}: Readonly<DrawerRepoListProps>) {
  const entries = switcherEntries(engines, defaultKey);
  const accents = resolveAccents(allEngines);
  if (entries.length === 0) return null;
  return (
    <nav
      aria-label="Repositories"
      data-testid="repo-drawer-switcher"
      className="flex flex-col gap-1 border-b border-border pb-3"
    >
      {entries.map((entry) => {
        const selected = entry.key === engineKey;
        const resolved = accents.get(entry.key) ?? null;
        return (
          <Link
            key={entry.key}
            to={`/repos/${entry.key}`}
            aria-current={ariaCurrentSelection(selected)}
            data-testid={`repo-drawer-repo-${entry.key}`}
            style={accentVars(resolved)}
            className={`${REPO_LINK_CLASS} ${selected ? accentEdgeClass(resolved) : "border-transparent"} ${selected ? accentTextClass(resolved) : SELECTION_TEXT_UNSELECTED_CLASS}`}
          >
            {entry.title}
          </Link>
        );
      })}
    </nav>
  );
}

export interface RepoDrawerProps {
  readonly engineKey: string;
  readonly onClose: () => void;
}

export default function RepoDrawer({ engineKey, onClose }: Readonly<RepoDrawerProps>) {
  const dialogRef = useModalLifecycle();
  const { engines, allEngines, defaultEngineKey } = useEngines();
  const resolvedAccent = resolveAccents(allEngines).get(engineKey) ?? null;

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions
    <dialog
      ref={dialogRef}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose();
      }}
      aria-label="Repository and conversations"
      data-testid="repo-drawer"
      className="fixed inset-0 m-0 h-screen w-screen max-w-none bg-surface/80 p-0"
    >
      <div className="flex h-full w-[85vw] max-w-sm flex-col gap-3 overflow-hidden border-r border-border bg-surface-elevated p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-primary">Repositories</h2>
          <Button variant="ghost" data-testid="repo-drawer-close" aria-label="Close" onClick={onClose}>
            ✕
          </Button>
        </div>
        <DrawerRepoList
          engineKey={engineKey}
          engines={engines}
          allEngines={allEngines}
          defaultKey={defaultEngineKey}
        />
        <div className="min-h-0 flex-1">
          <ConversationRail engineKey={engineKey} resolvedAccent={resolvedAccent} />
        </div>
      </div>
    </dialog>
  );
}
