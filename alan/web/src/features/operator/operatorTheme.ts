import type { ITheme } from "@xterm/xterm";

type ThemeKey = "background" | "foreground" | "cursor" | "cursorAccent" | "selectionBackground";

const THEME_TOKENS: readonly (readonly [ThemeKey, string])[] = [
  ["background", "--color-surface"],
  ["foreground", "--color-text-primary"],
  ["cursor", "--color-text-primary"],
  ["cursorAccent", "--color-surface"],
  ["selectionBackground", "--color-border"],
];

export function readOperatorTheme(root: Element = document.documentElement): ITheme {
  const styles = getComputedStyle(root);
  const theme: Partial<Record<ThemeKey, string>> = {};
  for (const [key, token] of THEME_TOKENS) {
    const value = styles.getPropertyValue(token).trim();
    if (value !== "") theme[key] = value;
  }
  return theme;
}
