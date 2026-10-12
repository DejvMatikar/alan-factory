import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { shortcutsBlocked } from "../lib/shortcutGuard";
import { matchShortcut } from "../lib/shortcuts";
import CommandPalette from "./CommandPalette";
import { CommandPaletteContext, type CommandPaletteStep } from "./commandPaletteContext";

const PALETTE_SHORTCUTS = ["palette", "build-direct", "explore-direct"] as const;

const STEP_FOR_SHORTCUT: Readonly<Record<(typeof PALETTE_SHORTCUTS)[number], CommandPaletteStep>> = {
  palette: { name: "root" },
  "build-direct": { name: "build-repo" },
  "explore-direct": { name: "explore-consultant" },
};

export function CommandPaletteProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [step, setStep] = useState<CommandPaletteStep | null>(null);
  const close = useCallback(() => setStep(null), []);
  const value = useMemo(() => ({ openPalette: setStep }), []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      const id = matchShortcut(e, PALETTE_SHORTCUTS);
      if (id === null || shortcutsBlocked(e.target)) return;
      e.preventDefault();
      setStep(STEP_FOR_SHORTCUT[id]);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <CommandPaletteContext.Provider value={value}>
      {children}
      {step !== null && <CommandPalette step={step} onAdvance={setStep} onClose={close} />}
    </CommandPaletteContext.Provider>
  );
}
