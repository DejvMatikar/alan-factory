import { readFileSync } from "node:fs";

import { expect, it } from "vitest";

import { ACCENT_COLORS } from "./api";

const PY_TUPLE = /ACCENT_COLORS: tuple\[AccentColor, \.\.\.\] = \(([^)]*)\)/;
const pySource = readFileSync("../repo_accent.py", "utf-8");
const pyColors = new Set([...(PY_TUPLE.exec(pySource)?.[1] ?? "").matchAll(/"([a-z]+)"/g)].map((m) => m[1]!));

it("keeps the accent colour set identical between repo_accent.py and api.ts", () => {
  expect(pyColors).toEqual(new Set(ACCENT_COLORS));
});

it("uses bare lowercase colour names only, with no digits, hyphens, or other separators", () => {
  expect(ACCENT_COLORS.every((c) => /^[a-z]+$/.test(c))).toBe(true);
});

it("never reintroduces lavender into the palette", () => {
  expect(ACCENT_COLORS).not.toContain("lavender");
});
