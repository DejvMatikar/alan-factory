import { describe, expect, it } from "vitest";

import type { HealthState } from "../api";
import { deriveHealthDisplay } from "./healthDisplay";

function healthState(overrides: Partial<HealthState> = {}): HealthState {
  return {
    verdict: "degraded",
    blocked_count: 0,
    in_flight_count: 0,
    findings: [],
    computed_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("deriveHealthDisplay — stale", () => {
  it("returns the stale state when healthState is null", () => {
    const display = deriveHealthDisplay({
      healthState: null,
      queuePaused: false,
      blockedCount: 0,
      needsUserCount: 0,
    });
    expect(display.state).toBe("stale");
    expect(display.dot).toBe("bg-gray-400");
    expect(display.ariaLabel).toBe("System health: Status unknown");
    expect(display.count).toBeNull();
  });
});

describe("deriveHealthDisplay — healthy", () => {
  it("returns the healthy state when blockedCount is 0 and not paused", () => {
    const display = deriveHealthDisplay({
      healthState: healthState({ verdict: "healthy" }),
      queuePaused: false,
      blockedCount: 0,
      needsUserCount: 0,
    });
    expect(display.state).toBe("healthy");
    expect(display.dot).toBe("bg-green-500");
    expect(display.ariaLabel).toBe("System health: 0 issues");
    expect(display.count).toBeNull();
  });
});

describe("deriveHealthDisplay — paused", () => {
  it("returns the paused state when queuePaused is true and blockedCount is 0", () => {
    const display = deriveHealthDisplay({
      healthState: healthState({ verdict: "healthy" }),
      queuePaused: true,
      blockedCount: 0,
      needsUserCount: 0,
    });
    expect(display.state).toBe("paused");
    expect(display.dot).toBe("bg-amber-400");
    expect(display.ariaLabel).toBe("System health: Paused");
    expect(display.count).toBeNull();
  });
});

describe("deriveHealthDisplay — degraded", () => {
  it("shows plural issues and the needs-you suffix", () => {
    const display = deriveHealthDisplay({
      healthState: healthState({ blocked_count: 3 }),
      queuePaused: false,
      blockedCount: 3,
      needsUserCount: 2,
    });
    expect(display.state).toBe("degraded");
    expect(display.dot).toBe("bg-red-500");
    expect(display.ariaLabel).toBe("System health: 3 issues · 2 needs you");
    expect(display.count).toBe(3);
  });

  it("shows singular issue when blockedCount is 1", () => {
    const display = deriveHealthDisplay({
      healthState: healthState({ blocked_count: 1 }),
      queuePaused: false,
      blockedCount: 1,
      needsUserCount: 1,
    });
    expect(display.ariaLabel).toBe("System health: 1 issue · 1 needs you");
  });

  it("omits the needs-you suffix when all blocked items are in-flight", () => {
    const display = deriveHealthDisplay({
      healthState: healthState({ blocked_count: 2 }),
      queuePaused: false,
      blockedCount: 2,
      needsUserCount: 0,
    });
    expect(display.ariaLabel).toBe("System health: 2 issues");
  });

  it("takes precedence over paused when both are true", () => {
    const display = deriveHealthDisplay({
      healthState: healthState({ blocked_count: 2 }),
      queuePaused: true,
      blockedCount: 2,
      needsUserCount: 0,
    });
    expect(display.state).toBe("degraded");
    expect(display.ariaLabel).not.toContain("Paused");
  });
});
