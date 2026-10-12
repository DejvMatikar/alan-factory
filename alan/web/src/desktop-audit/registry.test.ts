import { describe, expect, it } from "vitest";

import { validateDesktopStateRegistry } from "./registry";
import type { DesktopStateRegistry } from "./registry";

const SCREENS = ["home", "settings", "prep-chat"] as const;

function seeded(screen: string): { screen: string; maxState: { kind: "seeded"; uncovered: readonly string[] } } {
  return { screen, maxState: { kind: "seeded", uncovered: [] } };
}

function none(screen: string, reason: string): {
  screen: string;
  maxState: { kind: "none"; noMaxStateReason: string; uncovered: readonly string[] };
} {
  return { screen, maxState: { kind: "none", noMaxStateReason: reason, uncovered: [] } };
}

describe("validateDesktopStateRegistry", () => {
  it("passes a registry with exactly one entry per screen", () => {
    const registry: DesktopStateRegistry = [seeded("home"), seeded("settings"), none("prep-chat", "no network round-trip")];
    const result = validateDesktopStateRegistry([...SCREENS], registry);
    expect(result).toEqual({ valid: true, errors: [] });
  });

  it("fails when a screen is missing a registry entry", () => {
    const registry: DesktopStateRegistry = [seeded("home"), seeded("settings")];
    const result = validateDesktopStateRegistry([...SCREENS], registry);
    expect(result.valid).toBe(false);
    expect(result.errors).toContain('screen "prep-chat" is missing a desktop state registry entry');
  });

  it("fails when a screen has more than one registry entry", () => {
    const registry: DesktopStateRegistry = [seeded("home"), seeded("home"), seeded("settings"), seeded("prep-chat")];
    const result = validateDesktopStateRegistry([...SCREENS], registry);
    expect(result.errors).toContain('screen "home" has more than one registry entry');
  });

  it("fails when a registry entry is not in the screen registry", () => {
    const registry: DesktopStateRegistry = [
      seeded("home"),
      seeded("settings"),
      seeded("prep-chat"),
      seeded("ghost-screen"),
    ];
    const result = validateDesktopStateRegistry([...SCREENS], registry);
    expect(result.errors).toContain('registry entry "ghost-screen" is not in the screen registry');
  });

  it("fails when a 'none' maxState entry carries an empty noMaxStateReason", () => {
    const registry: DesktopStateRegistry = [seeded("home"), seeded("settings"), none("prep-chat", "   ")];
    const result = validateDesktopStateRegistry([...SCREENS], registry);
    expect(result.errors).toContain('screen "prep-chat" has maxState "none" but an empty noMaxStateReason');
  });

  it("fails when a screen name does not match the lowercase-kebab pattern", () => {
    const registry: DesktopStateRegistry = [seeded("Home"), seeded("settings"), seeded("prep-chat")];
    const result = validateDesktopStateRegistry(["Home", "settings", "prep-chat"], registry);
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes('screen name "Home"'))).toBe(true);
  });

  it("accumulates every distinct error across multiple bad entries", () => {
    const registry: DesktopStateRegistry = [seeded("home"), none("ghost", "")];
    const result = validateDesktopStateRegistry([...SCREENS], registry);
    expect(result.errors.length).toBeGreaterThan(1);
  });
});
