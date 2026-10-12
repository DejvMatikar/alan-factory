import { describe, expect, it } from "vitest";

import type { EngineFlowGraphPolicy, EngineRoleConfig } from "../../../api";
import { FIELD_LABEL, fieldSlug, summarizeFieldValue } from "./engineConfigFieldSummary";

const FLOW_POLICY: EngineFlowGraphPolicy = {
  verifier_teams: ["verification"],
  merge_teams: ["pr_merge"],
  terminal_teams: ["deploy_verify"],
  internal_only_teams: [],
  restricted_predecessors: {},
  revisit_cap: 3,
};

describe("summarizeFieldValue", () => {
  it("summarizes gate_commands and warmup_commands as joined argv lists", () => {
    expect(summarizeFieldValue("gate_commands", [["uv", "run", "pytest"], ["uv", "run", "ruff"]])).toBe(
      "uv run pytest | uv run ruff",
    );
    expect(summarizeFieldValue("warmup_commands", [])).toBe("—");
  });

  it("summarizes base_tools as a joined list, em-dash when empty", () => {
    expect(summarizeFieldValue("base_tools", ["Read", "Grep"])).toBe("Read, Grep");
    expect(summarizeFieldValue("base_tools", [])).toBe("—");
  });

  it("summarizes team_tools and transition_graph as sorted keyed lists", () => {
    expect(
      summarizeFieldValue("team_tools", { implementation: ["Edit"], architecture: [] }),
    ).toBe("architecture: —; implementation: Edit");
    expect(summarizeFieldValue("transition_graph", {})).toBe("—");
  });

  it("summarizes roles as sorted model/effort pairs, omitting effort when null", () => {
    const roles: Record<string, EngineRoleConfig> = {
      implementer: { model: "opus", effort: "high" },
      reviewer: { model: "haiku", effort: null },
    };
    expect(summarizeFieldValue("roles", roles)).toBe("implementer: opus/high; reviewer: haiku");
    expect(summarizeFieldValue("roles", {})).toBe("—");
  });

  it("summarizes flow_policy, and reports 'No flow policy' for null", () => {
    expect(summarizeFieldValue("flow_policy", FLOW_POLICY)).toBe(
      "verifier: verification · merge: pr_merge · terminal: deploy_verify · internal-only: — · revisit cap: 3",
    );
    expect(summarizeFieldValue("flow_policy", null)).toBe("No flow policy");
  });

  it("renders an em dash for every empty flow_policy team list, not just internal-only", () => {
    const empty: EngineFlowGraphPolicy = {
      verifier_teams: [],
      merge_teams: [],
      terminal_teams: [],
      internal_only_teams: [],
      restricted_predecessors: {},
      revisit_cap: 0,
    };
    expect(summarizeFieldValue("flow_policy", empty)).toBe(
      "verifier: — · merge: — · terminal: — · internal-only: — · revisit cap: 0",
    );
  });
});

describe("FIELD_LABEL", () => {
  it("labels exactly the seven proposable fields", () => {
    expect(Object.keys(FIELD_LABEL).sort()).toEqual(
      [
        "base_tools",
        "flow_policy",
        "gate_commands",
        "roles",
        "team_tools",
        "transition_graph",
        "warmup_commands",
      ].sort(),
    );
  });
});

describe("fieldSlug", () => {
  it("replaces underscores with hyphens", () => {
    expect(fieldSlug("gate_commands")).toBe("gate-commands");
    expect(fieldSlug("roles")).toBe("roles");
  });
});
