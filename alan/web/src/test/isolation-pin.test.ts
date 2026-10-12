import { describe, expect, it, vi } from "vitest";

describe("isolation pin: vi.spyOn residue", () => {
  const target = { m: () => "original" };

  it("test 1 leaves a spy installed", () => {
    vi.spyOn(target, "m").mockReturnValue("mocked");
    expect(target.m()).toBe("mocked");
  });

  it("test 2 asserts the spy was restored", () => {
    expect(vi.isMockFunction(target.m)).toBe(false);
    expect(target.m()).toBe("original");
  });
});

describe("isolation pin: vi.stubGlobal residue", () => {
  it("test 1 leaves a stubbed global", () => {
    vi.stubGlobal("__isolationPin", 1);
    expect((globalThis as Record<string, unknown>).__isolationPin).toBe(1);
  });

  it("test 2 asserts the global was unstubbed", () => {
    expect((globalThis as Record<string, unknown>).__isolationPin).toBeUndefined();
  });
});

describe("isolation pin: vi.useFakeTimers residue", () => {
  it("test 1 leaves fake timers active", () => {
    vi.useFakeTimers();
    expect(vi.isFakeTimers()).toBe(true);
  });

  it("test 2 asserts real timers were restored", () => {
    expect(vi.isFakeTimers()).toBe(false);
  });
});
