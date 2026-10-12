import { describe, expect, it } from "vitest";

import {
  PLANNING_STATE_LEGEND,
  planningStateGlyph,
  planningStateLabel,
} from "./planningState";

describe("planningStateLabel", () => {
  it("returns 'Detailed plan' for detailed", () => {
    expect(planningStateLabel("detailed")).toBe("Detailed plan");
  });
  it("returns 'Outline' for outline", () => {
    expect(planningStateLabel("outline")).toBe("Outline");
  });
});

describe("planningStateGlyph", () => {
  it("returns 'D' for detailed", () => {
    expect(planningStateGlyph("detailed")).toBe("D");
  });
  it("returns 'O' for outline", () => {
    expect(planningStateGlyph("outline")).toBe("O");
  });
});

describe("PLANNING_STATE_LEGEND", () => {
  it("contains both D and O explanations", () => {
    expect(PLANNING_STATE_LEGEND).toContain("D =");
    expect(PLANNING_STATE_LEGEND).toContain("O =");
  });
});
