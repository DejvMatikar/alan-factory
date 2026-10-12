import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { useCommandPalette } from "../components/commandPaletteContext";
import { useShellExplore } from "./ShellExploreProvider";

export interface RepoShortcutActions {
  readonly build: (scope: string | null) => void;
  readonly explore: (scope: string | null) => void;
}

export function useRepoShortcutActions(): RepoShortcutActions {
  const navigate = useNavigate();
  const { openExplore } = useShellExplore();
  const { openPalette } = useCommandPalette();

  return useMemo(
    () => ({
      build: (scope) => {
        if (scope === null) {
          openPalette({ name: "build-repo" });
          return;
        }
        void navigate(`/repos/${scope}/build?engine=${encodeURIComponent(scope)}`);
      },
      explore: (scope) => {
        if (scope === null) {
          openPalette({ name: "explore-consultant" });
          return;
        }
        openExplore(scope);
      },
    }),
    [navigate, openExplore, openPalette],
  );
}
