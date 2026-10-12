import { describe, expect, it } from "vitest";

import { formatMergeBlockLabel, MERGE_BLOCK_CLASSES } from "./mergeBlockLabel";

describe("formatMergeBlockLabel", () => {
  it("maps every known block class to a human-readable label", () => {
    for (const blockClass of MERGE_BLOCK_CLASSES) {
      const label = formatMergeBlockLabel(blockClass);
      expect(label).not.toBe(blockClass);
      expect(label.length).toBeGreaterThan(0);
    }
  });

  it("returns specific labels for representative classes", () => {
    expect(formatMergeBlockLabel("stale_review")).toBe("Stale Review");
    expect(formatMergeBlockLabel("sonar_hotspot")).toBe("SonarCloud Hotspot");
    expect(formatMergeBlockLabel("behind_plus_transient")).toBe(
      "Branch Out-of-Date",
    );
    expect(formatMergeBlockLabel("blocked_no_signal")).toBe(
      "Blocked (No Signal)",
    );
    expect(formatMergeBlockLabel("post_update_red")).toBe(
      "Failed After Branch Update",
    );
  });

  it("falls back to the raw value for an unknown block class", () => {
    expect(formatMergeBlockLabel("totally_unknown")).toBe("totally_unknown");
  });
});
