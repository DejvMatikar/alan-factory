import { describe, expect, it } from "vitest";

import { ACCENT_COLORS, type AccentIntensity, type EngineSummary } from "../api";
import {
  ACCENT_EDGE_CLASS,
  ACCENT_FRAME_PAD_BOTTOM_CLASS,
  ACCENT_FRAME_PAD_LEFT_CLASS,
  ACCENT_FRAME_PAD_RIGHT_CLASS,
  ACCENT_FRAME_PAD_TOP_CLASS,
  ACCENT_GLOW_MIN_ALPHA,
  ACCENT_GLOW_LEFT_CLASS,
  ACCENT_GLOW_RIGHT_CLASS,
  ACCENT_TEXT_CLASS,
  accentEdgeClass,
  accentEdgeHex,
  accentFgHex,
  accentGlowClass,
  accentGlowHex,
  accentTextClass,
  accentVars,
  resolveAccents,
} from "./repoAccent";
import { makeEngineSummary } from "../test/engineFixtures";

function engine(
  owner: string,
  repo: string,
  accent?: EngineSummary["accent"],
): EngineSummary {
  return makeEngineSummary({
    repo: { owner, repo },
    posture: "autonomous",
    ...(accent !== undefined ? { accent } : {}),
  });
}

const ALL_COLORS = ACCENT_COLORS;

describe("accentEdgeHex / accentFgHex", () => {
  it("returns the literal, retained standard-intensity anchors", () => {
    expect(accentEdgeHex("iris", "standard")).toBe("#8156e4");
    expect(accentEdgeHex("crimson", "standard")).toBe("#db245b");
  });

  it("returns a distinct edge hex per intensity for a given colour", () => {
    const soft = accentEdgeHex("plum", "soft");
    const standard = accentEdgeHex("plum", "standard");
    const bold = accentEdgeHex("plum", "bold");
    expect(new Set([soft, standard, bold]).size).toBe(3);
  });

  it("returns a distinct fg hex for every one of the sixteen colours", () => {
    const fgs = new Set(ALL_COLORS.map((color) => accentFgHex(color)));
    expect(fgs.size).toBe(ACCENT_COLORS.length);
  });
});

describe("resolveAccents — explicit choices", () => {
  it("honours an explicit colour and intensity", () => {
    const accents = resolveAccents([engine("acme", "widget", { color: "ruby", intensity: "bold" })]);
    expect(accents.get("acme/widget")).toEqual({ color: "ruby", intensity: "bold" });
  });

  it("resolves an explicit no-accent choice to null", () => {
    const accents = resolveAccents([engine("acme", "widget", { color: null, intensity: "standard" })]);
    expect(accents.get("acme/widget")).toBeNull();
  });

  it("treats a missing accent field and an explicit null the same — automatic assignment", () => {
    const withoutField = resolveAccents([engine("acme", "widget")]);
    const withNull = resolveAccents([engine("acme", "widget2", null)]);
    expect(withoutField.get("acme/widget")).not.toBeNull();
    expect(withNull.get("acme/widget2")).not.toBeNull();
  });
});

describe("resolveAccents — automatic assignment and collision avoidance", () => {
  it("assigns the same colour for the same key every time", () => {
    const engines = [engine("acme", "widget")];
    expect(resolveAccents(engines).get("acme/widget")).toEqual(
      resolveAccents(engines).get("acme/widget"),
    );
  });

  it("skips a colour already taken by another connected repository's explicit choice", () => {
    const accents = resolveAccents([
      engine("acme", "backend", { color: "magenta", intensity: "standard" }),
      engine("acme", "portal"),
    ]);
    expect(accents.get("acme/portal")?.color).not.toBe("magenta");
  });

  it("skips a colour already auto-assigned to another repository whose preferred slot collides", () => {
    const accents = resolveAccents([engine("acme", "api"), engine("acme", "gateway")]);
    const api = accents.get("acme/api");
    const gateway = accents.get("acme/gateway");
    expect(api?.color).toBe("orange");
    expect(gateway?.color).toBe("amber");
    expect(api?.color).not.toBe(gateway?.color);
  });

  it("resolves a three-way preferred-slot collision to three distinct colours", () => {
    const accents = resolveAccents([
      engine("acme", "sprocket"),
      engine("sprocket-co", "sprocket"),
      engine("repo-app-co", "widget"),
    ]);
    const colors = new Set(
      ["acme/sprocket", "sprocket-co/sprocket", "repo-app-co/widget"].map(
        (key) => accents.get(key)?.color,
      ),
    );
    expect(colors.size).toBe(3);
  });

  it("falls back to the preferred colour, allowing a collision, once every colour is already taken", () => {
    const explicitEngines = ALL_COLORS.map((color, i) =>
      engine("taken", `repo-${i}`, { color, intensity: "standard" }),
    );
    const accents = resolveAccents([...explicitEngines, engine("acme", "widget")]);
    expect(accents.get("acme/widget")?.color).toBe("teal");
  });

  it("never reshuffles an unrelated repository's colour when a new one connects and free colours remain", () => {
    const before = resolveAccents([engine("acme", "widget")]).get("acme/widget");
    const after = resolveAccents([
      engine("acme", "widget"),
      engine("brand-new-repo", "just-connected"),
    ]).get("acme/widget");
    expect(after).toEqual(before);
  });

  it("assigns every auto colour standard intensity", () => {
    const accents = resolveAccents([engine("acme", "widget")]);
    expect(accents.get("acme/widget")?.intensity).toBe("standard");
  });
});

describe("accentVars", () => {
  it("returns literal hex strings for a resolved accent, never a further var() reference", () => {
    const vars = accentVars({ color: "iris", intensity: "standard" });
    expect(vars).toEqual({
      "--accent-edge": "#8156e4",
      "--accent-fg": "#9e80e7",
      "--accent-glow": "#8156e499",
    });
    const edge = (vars as Record<string, string>)["--accent-edge"];
    expect(edge).not.toMatch(/var\(/);
  });

  it("returns an empty style object for an explicit no-accent resolution", () => {
    expect(accentVars(null)).toEqual({});
  });
});

describe("accentGlowHex", () => {
  const INTENSITIES: readonly AccentIntensity[] = ["soft", "standard", "bold"];

  it.each(ACCENT_COLORS)("returns a valid #RRGGBBAA string for every intensity of %s", (color) => {
    for (const intensity of INTENSITIES) {
      expect(accentGlowHex(color, intensity)).toMatch(/^#[0-9a-f]{8}$/i);
    }
  });

  it.each(ACCENT_COLORS)("increases glow alpha strictly from soft to standard to bold for %s", (color) => {
    const alphaOf = (intensity: AccentIntensity): number => parseInt(accentGlowHex(color, intensity).slice(7, 9), 16);
    const soft = alphaOf("soft");
    const standard = alphaOf("standard");
    const bold = alphaOf("bold");
    expect(soft).toBeLessThan(standard);
    expect(standard).toBeLessThan(bold);
    expect(soft).toBeGreaterThanOrEqual(ACCENT_GLOW_MIN_ALPHA);
  });
});

describe("accentGlowClass", () => {
  it("returns the left glow class for a resolved accent on the left side", () => {
    expect(accentGlowClass({ color: "iris", intensity: "standard" }, "left")).toBe(ACCENT_GLOW_LEFT_CLASS);
  });

  it("returns the right glow class for a resolved accent on the right side", () => {
    expect(accentGlowClass({ color: "iris", intensity: "standard" }, "right")).toBe(ACCENT_GLOW_RIGHT_CLASS);
  });

  it("returns an empty string on both sides for an explicit no-accent resolution", () => {
    expect(accentGlowClass(null, "left")).toBe("");
    expect(accentGlowClass(null, "right")).toBe("");
  });
});

describe("frame padding constants", () => {
  it("uses the identical numeric spacing step on left, right, top, and bottom", () => {
    expect(ACCENT_FRAME_PAD_LEFT_CLASS.slice(3)).toBe(ACCENT_FRAME_PAD_RIGHT_CLASS.slice(3));
    expect(ACCENT_FRAME_PAD_LEFT_CLASS.slice(3)).toBe(ACCENT_FRAME_PAD_TOP_CLASS.slice(3));
    expect(ACCENT_FRAME_PAD_LEFT_CLASS.slice(3)).toBe(ACCENT_FRAME_PAD_BOTTOM_CLASS.slice(3));
  });
});

describe("accent class shapes", () => {
  it("drives the edge colour from the shared --accent-edge variable", () => {
    expect(ACCENT_EDGE_CLASS).toContain("var(--accent-edge)");
  });

  it("drives the text colour from the shared --accent-fg variable", () => {
    expect(ACCENT_TEXT_CLASS).toContain("var(--accent-fg)");
  });

  it("accentEdgeClass returns the shared accent class for a resolved colour", () => {
    expect(accentEdgeClass({ color: "iris", intensity: "standard" })).toBe(ACCENT_EDGE_CLASS);
  });

  it("accentEdgeClass swaps to the neutral border token for explicit no-accent", () => {
    expect(accentEdgeClass(null)).toBe("border-border");
  });

  it("accentTextClass returns the shared accent class for a resolved colour", () => {
    expect(accentTextClass({ color: "iris", intensity: "standard" })).toBe(ACCENT_TEXT_CLASS);
  });

  it("accentTextClass swaps to the neutral text token for explicit no-accent", () => {
    expect(accentTextClass(null)).toBe("text-text-primary");
  });
});
