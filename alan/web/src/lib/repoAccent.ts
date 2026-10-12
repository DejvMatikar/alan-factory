import type { CSSProperties } from "react";

import { ACCENT_COLORS, type AccentColor, type AccentIntensity, type EngineSummary } from "../api";

export const ACCENT_EDGE_CLASS = "border-[var(--accent-edge)]";
export const ACCENT_TEXT_CLASS = "text-[var(--accent-fg)]";
export const ACCENT_GLOW_LEFT_CLASS = "shadow-[-6px_0_18px_var(--accent-glow)]";
export const ACCENT_GLOW_RIGHT_CLASS = "shadow-[6px_0_18px_var(--accent-glow)]";
export const ACCENT_FRAME_PAD_LEFT_CLASS = "pl-4";
export const ACCENT_FRAME_PAD_RIGHT_CLASS = "pr-4";
export const ACCENT_FRAME_PAD_TOP_CLASS = "pt-4";
export const ACCENT_FRAME_PAD_BOTTOM_CLASS = "pb-4";
export const ACCENT_GLOW_MIN_ALPHA = 0x66;

export type AccentGlowSide = "left" | "right";

export interface ResolvedAccent {
  readonly color: AccentColor;
  readonly intensity: AccentIntensity;
}

interface AccentColorTokens {
  readonly fg: string;
  readonly soft: string;
  readonly standard: string;
  readonly bold: string;
}

const ACCENT_TOKENS: Readonly<Record<AccentColor, AccentColorTokens>> = {
  iris: { fg: "#9e80e7", soft: "#846bbd", standard: "#8156e4", bold: "#9165f5" },
  violet: { fg: "#af79e5", soft: "#9067ba", standard: "#974de2", bold: "#a75af4" },
  plum: { fg: "#c170e4", soft: "#9e60b8", standard: "#ae3adf", bold: "#bf46f3" },
  orchid: { fg: "#d563e1", soft: "#ab59b4", standard: "#c222d3", bold: "#db17f0" },
  magenta: { fg: "#e05dd3", soft: "#b356aa", standard: "#cb21ba", bold: "#e70fd1" },
  rose: { fg: "#e163bb", soft: "#b45999", standard: "#d2229d", bold: "#f011ad" },
  ruby: { fg: "#e267a4", soft: "#b55b88", standard: "#d8237d", bold: "#f1268b" },
  crimson: { fg: "#e3698e", soft: "#b65d77", standard: "#db245b", bold: "#f22f69" },
  orange: { fg: "#e56c56", soft: "#c05743", standard: "#da3a20", bold: "#fa2d00" },
  amber: { fg: "#b28a44", soft: "#947134", standard: "#996e17", bold: "#b17d00" },
  tan: { fg: "#e3b8a0", soft: "#be9b87", standard: "#d2a48a", bold: "#e5ac8c" },
  green: { fg: "#6b9e4c", soft: "#57823d", standard: "#488518", bold: "#53a100" },
  teal: { fg: "#449ea0", soft: "#348284", standard: "#008588", bold: "#00acb1" },
  blue: { fg: "#5197ca", soft: "#3f7ca9", standard: "#2b7cb3", bold: "#1e88ca" },
  slate: { fg: "#9cc3d7", soft: "#8ba2ad", standard: "#90afbe", bold: "#94bbce" },
  bone: { fg: "#cdc3ae", soft: "#aca698", standard: "#b9b1a0", bold: "#c4bba6" },
};

const ACCENT_GLOW_ALPHA: Readonly<Record<AccentIntensity, string>> = {
  soft: "66",
  standard: "99",
  bold: "cc",
};

export function accentEdgeHex(color: AccentColor, intensity: AccentIntensity): string {
  return ACCENT_TOKENS[color][intensity];
}

export function accentFgHex(color: AccentColor): string {
  return ACCENT_TOKENS[color].fg;
}

export function accentGlowHex(color: AccentColor, intensity: AccentIntensity): string {
  return `${accentEdgeHex(color, intensity)}${ACCENT_GLOW_ALPHA[intensity]}`;
}

export function accentGlowClass(resolved: ResolvedAccent | null, side: AccentGlowSide): string {
  if (resolved === null) return "";
  return side === "left" ? ACCENT_GLOW_LEFT_CLASS : ACCENT_GLOW_RIGHT_CLASS;
}

function engineAccentKey(engine: EngineSummary): string {
  return `${engine.repo.owner}/${engine.repo.repo}`;
}

function hashKey(key: string): number {
  let hash = 5381;
  for (let i = 0; i < key.length; i += 1) {
    hash = ((hash * 33) ^ key.codePointAt(i)!) >>> 0;
  }
  return hash;
}

function preferredColorIndex(key: string): number {
  return hashKey(key) % ACCENT_COLORS.length;
}

function pickFreeColor(key: string, taken: ReadonlySet<AccentColor>): AccentColor {
  const preferred = preferredColorIndex(key);
  if (taken.size < ACCENT_COLORS.length) {
    for (let offset = 0; offset < ACCENT_COLORS.length; offset += 1) {
      const candidate = ACCENT_COLORS[(preferred + offset) % ACCENT_COLORS.length]!;
      if (!taken.has(candidate)) return candidate;
    }
  }
  return ACCENT_COLORS[preferred]!;
}

export function resolveAccents(
  engines: readonly EngineSummary[],
): ReadonlyMap<string, ResolvedAccent | null> {
  const result = new Map<string, ResolvedAccent | null>();
  const taken = new Set<AccentColor>();
  const autoKeys: string[] = [];

  for (const engine of engines) {
    const key = engineAccentKey(engine);
    const accent = engine.accent;
    if (accent == null) {
      autoKeys.push(key);
      continue;
    }
    if (accent.color == null) {
      result.set(key, null);
      continue;
    }
    result.set(key, { color: accent.color, intensity: accent.intensity });
    taken.add(accent.color);
  }

  for (const key of autoKeys) {
    const chosen = pickFreeColor(key, taken);
    result.set(key, { color: chosen, intensity: "standard" });
    taken.add(chosen);
  }

  return result;
}

export function accentVars(resolved: ResolvedAccent | null): CSSProperties {
  if (resolved === null) return {};
  return {
    "--accent-edge": accentEdgeHex(resolved.color, resolved.intensity),
    "--accent-fg": accentFgHex(resolved.color),
    "--accent-glow": accentGlowHex(resolved.color, resolved.intensity),
  } as CSSProperties;
}

export function accentEdgeClass(resolved: ResolvedAccent | null): string {
  return resolved === null ? "border-border" : ACCENT_EDGE_CLASS;
}

export function accentTextClass(resolved: ResolvedAccent | null): string {
  return resolved === null ? "text-text-primary" : ACCENT_TEXT_CLASS;
}
