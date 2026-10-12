import { createContext, useContext } from "react";

export type CommandPaletteStep =
  | { name: "root" }
  | { name: "build-repo" }
  | { name: "explore-consultant" }
  | { name: "explore-repo"; consultantKey: string; consultantLabel: string };

interface CommandPaletteValue {
  readonly openPalette: (step: CommandPaletteStep) => void;
}

const _defaultValue: CommandPaletteValue = {
  openPalette: () => undefined,
};

export const CommandPaletteContext = createContext<CommandPaletteValue>(_defaultValue);

export function useCommandPalette(): CommandPaletteValue {
  return useContext(CommandPaletteContext);
}
