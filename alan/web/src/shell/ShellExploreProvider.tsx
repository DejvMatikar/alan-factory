import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

export interface ExploreTarget {
  readonly repoKey: string;
}

export interface ShellExploreValue {
  target: ExploreTarget | null;
  openExplore: (repoKey: string) => void;
  closeExplore: () => void;
}

const _defaultValue: ShellExploreValue = {
  target: null,
  openExplore: () => undefined,
  closeExplore: () => undefined,
};

export const ShellExploreContext = createContext<ShellExploreValue>(_defaultValue);

export function useShellExplore(): ShellExploreValue {
  return useContext(ShellExploreContext);
}

export function ShellExploreProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [target, setTarget] = useState<ExploreTarget | null>(null);

  const openExplore = useCallback((repoKey: string): void => {
    setTarget({ repoKey });
  }, []);

  const closeExplore = useCallback((): void => {
    setTarget(null);
  }, []);

  const value = useMemo(
    () => ({ target, openExplore, closeExplore }),
    [target, openExplore, closeExplore],
  );

  return <ShellExploreContext.Provider value={value}>{children}</ShellExploreContext.Provider>;
}
