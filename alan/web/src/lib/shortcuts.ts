import type { ShortcutHintProps } from "../components/ShortcutHint";
import { isApplePlatform } from "./platform";

export type ShortcutId =
  | "palette"
  | "build-direct"
  | "explore-direct"
  | "build-repo"
  | "explore-repo"
  | "modal-confirm"
  | "operator-escape";

interface ShortcutRow {
  readonly matches: (e: KeyboardEvent) => boolean;
  readonly apple: ShortcutHintProps;
  readonly other: ShortcutHintProps;
}

function hasMod(e: KeyboardEvent): boolean {
  return e.ctrlKey || e.metaKey;
}

function isCtrlShift(e: KeyboardEvent, code: string): boolean {
  return e.ctrlKey && e.shiftKey && e.code === code;
}

const SHORTCUTS: Readonly<Record<ShortcutId, ShortcutRow>> = {
  palette: {
    matches: (e) => hasMod(e) && !e.altKey && e.code === "KeyK",
    apple: { glyph: "⌘K", spoken: "Command K" },
    other: { glyph: "Ctrl K", spoken: "Control K" },
  },
  "build-direct": {
    matches: (e) => hasMod(e) && e.altKey && e.code === "KeyB",
    apple: { glyph: "⌥⌘B", spoken: "Option Command B" },
    other: { glyph: "Ctrl Alt B", spoken: "Control Alt B" },
  },
  "explore-direct": {
    matches: (e) => hasMod(e) && e.altKey && e.code === "KeyC",
    apple: { glyph: "⌥⌘C", spoken: "Option Command C" },
    other: { glyph: "Ctrl Alt C", spoken: "Control Alt C" },
  },
  "build-repo": {
    matches: (e) => isCtrlShift(e, "KeyX"),
    apple: { glyph: "⌃⇧X", spoken: "Control Shift X" },
    other: { glyph: "Ctrl Shift X", spoken: "Control Shift X" },
  },
  "explore-repo": {
    matches: (e) => isCtrlShift(e, "KeyE"),
    apple: { glyph: "⌃⇧E", spoken: "Control Shift E" },
    other: { glyph: "Ctrl Shift E", spoken: "Control Shift E" },
  },
  "modal-confirm": {
    matches: (e) => e.ctrlKey && e.key === "Enter",
    apple: { glyph: "⌃↵", spoken: "Control Enter" },
    other: { glyph: "Ctrl Enter", spoken: "Control Enter" },
  },
  "operator-escape": {
    matches: (e) => isCtrlShift(e, "KeyF"),
    apple: { glyph: "⌃⇧F", spoken: "Control Shift F" },
    other: { glyph: "Ctrl Shift F", spoken: "Control Shift F" },
  },
};

export function shortcutHint(id: ShortcutId): ShortcutHintProps {
  const row = SHORTCUTS[id];
  return isApplePlatform() ? row.apple : row.other;
}

export function matchShortcut<T extends ShortcutId>(
  e: KeyboardEvent,
  ids: readonly T[],
): T | null {
  return ids.find((id) => SHORTCUTS[id].matches(e)) ?? null;
}
