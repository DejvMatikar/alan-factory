import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ACCENT_COLORS, ACCENT_INTENSITIES, type AccentColor } from "./api";
import { accentEdgeHex, accentFgHex } from "./lib/repoAccent";
import { contrastLargeTextMinRatio, contrastNormalTextMinRatio } from "./ui-spec";

const CSS_PATH = path.resolve(process.cwd(), "src/index.css");
const TEXT_SEPARATION_MIN_RATIO = 1.35;
const OKLAB_SEPARATION_MIN = 0.075;
const NEAR_NEUTRAL_ACCENTS = ["slate", "bone"] as const satisfies readonly AccentColor[];

function readColorToken(css: string, name: string): string {
  const match = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(css);
  if (match?.[1] === undefined) throw new Error(`token not found: ${name}`);
  return match[1];
}

function hexToRgb(hex: string): readonly [number, number, number] {
  const value = hex.replace("#", "");
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
  ];
}

function linearizeChannel(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex);
  return 0.2126 * linearizeChannel(r) + 0.7152 * linearizeChannel(g) + 0.0722 * linearizeChannel(b);
}

function contrastRatio(hexA: string, hexB: string): number {
  const luminanceA = relativeLuminance(hexA);
  const luminanceB = relativeLuminance(hexB);
  const lighter = Math.max(luminanceA, luminanceB);
  const darker = Math.min(luminanceA, luminanceB);
  return (lighter + 0.05) / (darker + 0.05);
}

function srgbChannelToLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function linearSrgbToOklab(r: number, g: number, b: number): readonly [number, number, number] {
  const l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b;
  const m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b;
  const s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b;
  const l_ = Math.cbrt(l);
  const m_ = Math.cbrt(m);
  const s_ = Math.cbrt(s);
  return [
    0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_,
    1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
    0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
  ];
}

function hexToOklab(hex: string): readonly [number, number, number] {
  const [r, g, b] = hexToRgb(hex);
  return linearSrgbToOklab(srgbChannelToLinear(r), srgbChannelToLinear(g), srgbChannelToLinear(b));
}

function parseStatusFgLch(css: string, statusName: string): readonly [number, number, number] {
  const pattern = new RegExp(
    `--color-status-${statusName}-fg:\\s*oklch\\(([\\d.]+)%\\s+([\\d.]+)\\s+([\\d.]+)\\)`,
  );
  const match = pattern.exec(css);
  if (match?.[1] === undefined || match[2] === undefined || match[3] === undefined) {
    throw new Error(`status token not found: ${statusName}`);
  }
  return [Number(match[1]) / 100, Number(match[2]), Number(match[3])];
}

function statusFgOklab(css: string, statusName: string): readonly [number, number, number] {
  const [lightness, chroma, hueDeg] = parseStatusFgLch(css, statusName);
  const hueRad = (hueDeg * Math.PI) / 180;
  return [lightness, chroma * Math.cos(hueRad), chroma * Math.sin(hueRad)];
}

function oklchToSrgbHex(lightness: number, chroma: number, hueDeg: number): string {
  const hueRad = (hueDeg * Math.PI) / 180;
  const a = chroma * Math.cos(hueRad);
  const b = chroma * Math.sin(hueRad);
  const l_ = lightness + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = lightness - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = lightness - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;
  const rLin = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const gLin = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const bLin = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;
  const toChannel = (channel: number) => {
    const clamped = Math.max(0, Math.min(1, channel));
    const encoded =
      clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055;
    return Math.round(encoded * 255)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${toChannel(rLin)}${toChannel(gLin)}${toChannel(bLin)}`;
}

function statusFgHex(css: string, statusName: string): string {
  const [lightness, chroma, hueDeg] = parseStatusFgLch(css, statusName);
  return oklchToSrgbHex(lightness, chroma, hueDeg);
}

function oklabDistance(a: readonly [number, number, number], b: readonly [number, number, number]): number {
  return Math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2);
}

const css = readFileSync(CSS_PATH, "utf-8");
const surfaceHex = readColorToken(css, "--color-surface");
const surfaceElevatedHex = readColorToken(css, "--color-surface-elevated");
const textPrimaryHex = readColorToken(css, "--color-text-primary");
const textMutedHex = readColorToken(css, "--color-text-muted");

const accentValues = ACCENT_COLORS.flatMap((color) => [
  { color, role: "fg", hex: accentFgHex(color) },
  ...ACCENT_INTENSITIES.map((intensity) => ({
    color,
    role: intensity,
    hex: accentEdgeHex(color, intensity),
  })),
]);

const HUE_COLLISION_PAIRS: readonly { readonly accent: AccentColor; readonly status: string }[] = [
  { accent: "blue", status: "active" },
  { accent: "amber", status: "attention" },
  { accent: "amber", status: "moderate" },
  { accent: "orange", status: "warning" },
  { accent: "green", status: "success" },
  { accent: "slate", status: "neutral" },
];

describe("repository accent contrast", () => {
  it.each(accentValues.filter((v) => v.role === "fg"))(
    "accent-$color-fg meets the normal-text contrast minimum against both surfaces",
    ({ hex }) => {
      expect(contrastRatio(hex, surfaceHex)).toBeGreaterThanOrEqual(contrastNormalTextMinRatio);
      expect(contrastRatio(hex, surfaceElevatedHex)).toBeGreaterThanOrEqual(contrastNormalTextMinRatio);
    },
  );

  it.each(accentValues.filter((v) => v.role !== "fg"))(
    "accent-$color-$role meets the large-text contrast minimum against both surfaces",
    ({ hex }) => {
      expect(contrastRatio(hex, surfaceHex)).toBeGreaterThanOrEqual(contrastLargeTextMinRatio);
      expect(contrastRatio(hex, surfaceElevatedHex)).toBeGreaterThanOrEqual(contrastLargeTextMinRatio);
    },
  );

  it.each(NEAR_NEUTRAL_ACCENTS)(
    "%s-fg stays distinguishable from ordinary primary and muted text at >= 1.35 contrast",
    (color) => {
      const hex = accentFgHex(color);
      expect(contrastRatio(hex, textPrimaryHex)).toBeGreaterThanOrEqual(TEXT_SEPARATION_MIN_RATIO);
      expect(contrastRatio(hex, textMutedHex)).toBeGreaterThanOrEqual(TEXT_SEPARATION_MIN_RATIO);
    },
  );

  it.each(HUE_COLLISION_PAIRS)(
    "$accent-fg stays >= 0.075 OKLab distance from the paired status-$status-fg it could be mistaken for",
    ({ accent, status }) => {
      const accentOklab = hexToOklab(accentFgHex(accent));
      const statusOklab = statusFgOklab(css, status);
      expect(oklabDistance(accentOklab, statusOklab)).toBeGreaterThanOrEqual(OKLAB_SEPARATION_MIN);
    },
  );
});

describe("unproven-clause marker contrast", () => {
  it("clears the normal-text contrast minimum for --color-status-warning-fg against both surfaces", () => {
    const markerHex = statusFgHex(css, "warning");
    expect(contrastRatio(markerHex, surfaceHex)).toBeGreaterThanOrEqual(contrastNormalTextMinRatio);
    expect(contrastRatio(markerHex, surfaceElevatedHex)).toBeGreaterThanOrEqual(
      contrastNormalTextMinRatio,
    );
  });
});
