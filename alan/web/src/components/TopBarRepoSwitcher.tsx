import { useState } from "react";
import { Link, useMatch } from "react-router-dom";

import Button from "./Button";
import HoverReveal from "./HoverReveal";
import {
  ariaCurrentSelection,
  SELECTION_INTERACTIVE_CLASS,
  SELECTION_TEXT_UNSELECTED_CLASS,
} from "../shell/selection";
import { accentEdgeClass, accentTextClass, accentVars, resolveAccents } from "../lib/repoAccent";
import RepoDrawer from "../shell/RepoDrawer";
import { switcherEntries, type RepoRegistryEntry } from "../lib/fold";
import { useEngines } from "../hooks/useEngines";
import { useRepoScope } from "../hooks/useRepoScope";

const ENTRY_BASE_CLASS = `inline-flex min-h-11 shrink-0 items-center border-b-2 px-2 text-sm font-medium ${SELECTION_INTERACTIVE_CLASS}`;

export function resolveDrawerEngineKey(
  scopedKey: string | null,
  entries: readonly RepoRegistryEntry[],
): string | null {
  return scopedKey ?? entries[0]?.key ?? null;
}

export default function TopBarRepoSwitcher() {
  const { engines, allEngines, defaultEngineKey } = useEngines();
  const { key: scopedKey } = useRepoScope(engines, defaultEngineKey);
  const entries = switcherEntries(engines, defaultEngineKey);
  const accents = resolveAccents(allEngines);
  const inRepoShell = useMatch("/repos/:owner/:repo/*") !== null;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const hasEntries = entries.length > 0;
  const drawerEngineKey = resolveDrawerEngineKey(scopedKey, entries);

  return (
    <>
      {hasEntries && (
        <nav
          aria-label="Repositories"
          data-testid="top-bar-repo-switcher"
          className="hidden min-w-0 flex-nowrap items-center gap-1 overflow-x-auto sm:flex"
        >
          {entries.map((entry) => {
            const selected = entry.key === scopedKey;
            const resolved = accents.get(entry.key) ?? null;
            return (
              <Link
                key={entry.key}
                to={`/repos/${entry.key}`}
                aria-current={ariaCurrentSelection(selected)}
                data-testid={`top-bar-repo-${entry.key}`}
                style={accentVars(resolved)}
                className={`${ENTRY_BASE_CLASS} ${selected ? accentEdgeClass(resolved) : "border-transparent"} ${selected ? accentTextClass(resolved) : SELECTION_TEXT_UNSELECTED_CLASS}`}
              >
                {entry.title}
              </Link>
            );
          })}
        </nav>
      )}
      {drawerEngineKey !== null && !inRepoShell && (
        <>
          <Button
            variant="ghost"
            data-testid="top-bar-repo-drawer-trigger"
            aria-label="Switch repository"
            className="min-h-11 sm:hidden"
            onClick={() => setDrawerOpen(true)}
          >
            Repositories
          </Button>
          {drawerOpen && (
            <RepoDrawer
              engineKey={drawerEngineKey}
              onClose={() => setDrawerOpen(false)}
            />
          )}
        </>
      )}
      <HoverReveal
        id="connect-repo-cta-description"
        description="Connect a GitHub repository so alan can start working in it."
      >
        <Button variant="create" linkTo="/connect" data-testid="connect-repo-cta">
          Connect repo
        </Button>
      </HoverReveal>
    </>
  );
}
