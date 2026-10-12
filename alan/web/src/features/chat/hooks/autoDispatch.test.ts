import { describe, expect, it, vi } from "vitest";

import {
  advance,
  stage,
  stageLabel,
  stopMessage,
  AUTO_DISPATCH_IDLE,
  AUTO_DISPATCH_UNSET,
  type AutoDispatchStage,
  type AutoDispatchStop,
} from "./autoDispatch";

describe("AUTO_DISPATCH_IDLE", () => {
  it("is the idle state", () => {
    expect(AUTO_DISPATCH_IDLE).toEqual({ running: false, stage: null, stop: null });
  });
});

describe("stageLabel", () => {
  const stages: AutoDispatchStage[] = ["summarizing", "checking", "proposing", "dispatching"];

  it.each(stages)("returns non-empty text for stage %s", (s) => {
    expect(stageLabel(s).length).toBeGreaterThan(0);
  });
});

describe("stopMessage", () => {
  const stops: AutoDispatchStop[] = [
    "overlap",
    "summarize_failed",
    "propose_failed",
    "needs_info",
    "brief_edited",
    "dispatch_failed",
    "cancelled",
  ];

  it.each(stops)("returns non-empty text for stop %s", (s) => {
    expect(stopMessage(s).length).toBeGreaterThan(0);
  });

  it("distinguishes every stop reason with its own message", () => {
    const messages = new Set(stops.map((s) => stopMessage(s)));
    expect(messages.size).toBe(stops.length);
  });
});

describe("stage", () => {
  it("resolves to a value outcome when the check passes and the run is not stale", async () => {
    const result = await stage(Promise.resolve(42), (v) => ({ value: v }), () => false);
    expect(result).toEqual({ value: 42 });
  });

  it("resolves to a stop outcome when the check fails", async () => {
    const result = await stage(Promise.resolve(42), () => ({ stop: "overlap" as const }), () => false);
    expect(result).toEqual({ stop: "overlap" });
  });

  it("resolves to stale without calling check when the run has gone stale", async () => {
    const check = vi.fn((v: number) => ({ value: v }));
    const result = await stage(Promise.resolve(42), check, () => true);
    expect(result).toBe("stale");
    expect(check).not.toHaveBeenCalled();
  });
});

describe("advance", () => {
  it("returns the value and does not call onStop for a value outcome", () => {
    const onStop = vi.fn();
    const result = advance({ value: 7 }, onStop);
    expect(result).toBe(7);
    expect(onStop).not.toHaveBeenCalled();
  });

  it("calls onStop and returns UNSET for a stop outcome", () => {
    const onStop = vi.fn();
    const result = advance({ stop: "dispatch_failed" }, onStop);
    expect(result).toBe(AUTO_DISPATCH_UNSET);
    expect(onStop).toHaveBeenCalledWith("dispatch_failed");
  });

  it("returns UNSET without calling onStop for a stale outcome", () => {
    const onStop = vi.fn();
    const result = advance("stale", onStop);
    expect(result).toBe(AUTO_DISPATCH_UNSET);
    expect(onStop).not.toHaveBeenCalled();
  });
});
