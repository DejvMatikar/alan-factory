import { describe, expect, it } from "vitest";

import type { EnginePosture } from "../../../api";
import { maintenanceGate, MENU_DISPATCH_MIN_POSTURE, POSTURE_RANK, posturePathFor } from "./postureGate";

describe("POSTURE_RANK", () => {
  it("ranks postures in ascending strictness order", () => {
    expect(POSTURE_RANK.observe).toBeLessThan(POSTURE_RANK.propose);
    expect(POSTURE_RANK.propose).toBeLessThan(POSTURE_RANK.self_merge);
    expect(POSTURE_RANK.self_merge).toBeLessThan(POSTURE_RANK.autonomous);
  });
});

describe("MENU_DISPATCH_MIN_POSTURE", () => {
  it("is autonomous, mirroring maintenance's server-side floor", () => {
    expect(MENU_DISPATCH_MIN_POSTURE).toBe("autonomous");
  });
});

const ALL_POSTURES: (EnginePosture | null)[] = ["observe", "propose", "self_merge", "autonomous", null];

describe("maintenanceGate — unresolved", () => {
  it.each(ALL_POSTURES)(
    "is unresolved regardless of posture (%s) while the fetch hasn't settled",
    (posture) => {
      expect(maintenanceGate(false, posture)).toEqual({ kind: "unresolved" });
    },
  );
});

describe("maintenanceGate — resolved and permitted", () => {
  it("is permitted with autonomous posture once resolved", () => {
    expect(maintenanceGate(true, "autonomous")).toEqual({ kind: "permitted" });
  });
});

describe("maintenanceGate — resolved and gated", () => {
  it.each<[EnginePosture, string]>([
    ["observe", "observe"],
    ["propose", "propose"],
    ["self_merge", "self_merge"],
  ])("gates at %s posture with a stated reason naming it", (posture, expectedWord) => {
    const gate = maintenanceGate(true, posture);
    if (gate.kind !== "gated") throw new Error("expected a gated result");
    expect(gate.reason).toMatch(/autonomous/i);
    expect(gate.reason).toMatch(new RegExp(expectedWord, "i"));
  });

  it("gates with a reason distinguishing an unreadable posture from a merely low one", () => {
    const gate = maintenanceGate(true, null);
    if (gate.kind !== "gated") throw new Error("expected a gated result");
    expect(gate.reason).toMatch(/couldn't be read/i);
  });
});

describe("posturePathFor", () => {
  it("builds the connect-wizard posture path from an owner/repo key", () => {
    expect(posturePathFor("acme/widget")).toBe("/connect/acme/widget/posture");
  });

  it("percent-encodes owner and repo segments", () => {
    expect(posturePathFor("ac me/wid get")).toBe("/connect/ac%20me/wid%20get/posture");
  });
});
