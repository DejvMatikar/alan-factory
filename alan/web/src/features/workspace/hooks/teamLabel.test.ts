import { describe, expect, it } from "vitest";

import { formatTeamLabel } from "./teamLabel";

describe("formatTeamLabel", () => {
  it("maps known team names to readable labels", () => {
    expect(formatTeamLabel("implementation_plan")).toBe("Implementation Plan");
    expect(formatTeamLabel("implementation")).toBe("Implementation");
    expect(formatTeamLabel("implementation_fix")).toBe("Implementation Fix");
    expect(formatTeamLabel("verification")).toBe("Verification");
    expect(formatTeamLabel("research")).toBe("Research");
    expect(formatTeamLabel("architecture")).toBe("Architecture");
    expect(formatTeamLabel("pr_merge")).toBe("PR Merge");
    expect(formatTeamLabel("decomposer")).toBe("Decomposer");
    expect(formatTeamLabel("triage")).toBe("Triage");
  });

  it("title-cases unknown underscore-separated names", () => {
    expect(formatTeamLabel("some_new_team")).toBe("Some New Team");
  });

  it("title-cases unknown hyphen-separated names", () => {
    expect(formatTeamLabel("my-custom-team")).toBe("My Custom Team");
  });

  it("returns a single capitalized word for a single segment", () => {
    expect(formatTeamLabel("alpha")).toBe("Alpha");
  });
});
