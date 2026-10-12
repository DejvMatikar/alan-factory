import { useEffect } from "react";

import { useEngines } from "../hooks/useEngines";
import { useRepoScope } from "../hooks/useRepoScope";
import { shortcutsBlocked } from "../lib/shortcutGuard";
import { matchShortcut } from "../lib/shortcuts";
import { useRepoShortcutActions } from "./useRepoShortcutActions";

const REPO_SHORTCUTS = ["build-repo", "explore-repo"] as const;

export default function ShellShortcuts() {
  const { engines, defaultEngineKey } = useEngines();
  const { key: scope } = useRepoScope(engines, defaultEngineKey);
  const actions = useRepoShortcutActions();

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      const id = matchShortcut(e, REPO_SHORTCUTS);
      if (id === null || shortcutsBlocked(e.target)) return;
      e.preventDefault();
      if (id === "build-repo") actions.build(scope);
      else actions.explore(scope);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [scope, actions]);

  return null;
}
