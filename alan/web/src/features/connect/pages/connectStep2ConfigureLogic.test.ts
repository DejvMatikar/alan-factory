import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, ConfigProposalBlockedError, type ConfigProposalResult, type EngineConfig } from "../../../api";
import {
  expectFullWireParity,
  makeConfiguredEngineConfig,
  PROJECTION_EXCLUDED_FIELDS,
  type WireEngineConfig,
} from "../../../test/engineFixtures";
import {
  buildStep2Payload,
  fallbackFindingOf,
  invalidateStep2Cache,
  parseStep2PoolSizeInput,
  readStep2Cache,
  seedDraft,
  seedDraftFromLive,
  toDiffRows,
  type Step2Draft,
  writeStep2Cache,
} from "./connectStep2ConfigureLogic";

function proposedConfig(): WireEngineConfig {
  return makeConfiguredEngineConfig({
    gate_commands: [["uv", "run", "pytest", "-q"]],
    warmup_commands: [["uv", "sync"]],
    base_tools: ["Read", "Grep"],
    team_tools: { implementation: ["Edit", "Write"] },
    roles: { implementer: { model: "opus", effort: "high" } },
    transition_graph: { research: ["architecture"], architecture: ["implementation"] },
    flow_policy: {
      verifier_teams: ["verification"],
      merge_teams: ["pr_merge"],
      terminal_teams: [],
      internal_only_teams: [],
      restricted_predecessors: {},
      revisit_cap: 3,
    },
  });
}

function makeProposal(overrides: Partial<ConfigProposalResult> = {}): ConfigProposalResult {
  const proposed = proposedConfig();
  return {
    slug: "acme/widget",
    base_preset: "python",
    detected_stacks: ["python"],
    additional_stacks: [],
    proposed_config: proposed,
    diff: [
      { field: "gate_commands", baseline: [["uv", "run", "pytest"]], proposed: proposed.gate_commands, changed: true },
      { field: "warmup_commands", baseline: [["uv", "sync"]], proposed: proposed.warmup_commands, changed: false },
      { field: "base_tools", baseline: ["Read"], proposed: proposed.base_tools, changed: true },
      { field: "team_tools", baseline: { implementation: ["Edit"] }, proposed: proposed.team_tools, changed: true },
      {
        field: "roles",
        baseline: { implementer: { model: "sonnet", effort: "high" } },
        proposed: proposed.roles,
        changed: true,
      },
      {
        field: "transition_graph",
        baseline: { research: ["architecture"], architecture: [] },
        proposed: proposed.transition_graph,
        changed: true,
      },
      { field: "flow_policy", baseline: null, proposed: proposed.flow_policy, changed: true },
    ],
    validation: { check: "flow_graph_and_posture_valid", status: "pass", message: "ok", remedy: "", remedy_url: "" },
    ...overrides,
  };
}

describe("seedDraft", () => {
  it("seeds the seven proposable fields from proposed_config and code_quality/pool_size from live", () => {
    const live = makeConfiguredEngineConfig({
      code_quality: { project_key: "k", organization: "o", host_url: "h", token_env_var: "t" },
      pool_size: 4,
    });
    const proposal = makeProposal();
    const draft = seedDraft(proposal, live);
    expect(draft.gate_commands).toEqual(proposal.proposed_config.gate_commands);
    expect(draft.flow_policy).toEqual(proposal.proposed_config.flow_policy);
    expect(draft.code_quality).toEqual(live.code_quality);
    expect(draft.pool_size).toBe(4);
  });

  it("seeds a null flow_policy, code_quality and pool_size as explicit nulls", () => {
    const proposal = makeProposal({ proposed_config: makeConfiguredEngineConfig() });
    const draft = seedDraft(proposal, makeConfiguredEngineConfig());
    expect(draft.flow_policy).toBeNull();
    expect(draft.code_quality).toBeNull();
    expect(draft.pool_size).toBeNull();
  });
});

describe("seedDraftFromLive", () => {
  it("seeds every field directly from the live engine, with no proposal involved", () => {
    const live = makeConfiguredEngineConfig({ pool_size: 2 });
    const draft = seedDraftFromLive(live);
    expect(draft.gate_commands).toEqual(live.gate_commands);
    expect(draft.roles).toEqual(live.roles);
    expect(draft.pool_size).toBe(2);
    expect(draft.flow_policy).toBeNull();
    expect(draft.code_quality).toBeNull();
  });

  it("seeds a present flow_policy and code_quality unchanged", () => {
    const live = makeConfiguredEngineConfig({
      code_quality: { project_key: "k", organization: "o", host_url: "h", token_env_var: "t" },
      flow_policy: proposedConfig().flow_policy,
    });
    const draft = seedDraftFromLive(live);
    expect(draft.flow_policy).toEqual(live.flow_policy);
    expect(draft.code_quality).toEqual(live.code_quality);
  });
});

describe("buildStep2Payload", () => {
  it("carries live's posture, forge, ci, repo, preset and prompt_overrides through unchanged", () => {
    const live = makeConfiguredEngineConfig({ posture: "propose", preset: "node" });
    const draft = seedDraftFromLive(live);
    const payload = buildStep2Payload(live, draft);
    expect(payload.posture).toBe("propose");
    expect(payload.forge).toEqual(live.forge);
    expect(payload.repo).toEqual(live.repo);
    expect(payload.preset).toBe("node");
  });

  it("carries the draft's edited proposable/baseline fields over live's own values", () => {
    const live = makeConfiguredEngineConfig();
    const draft = seedDraftFromLive(live);
    const edited = { ...draft, pool_size: 8, base_tools: ["Read", "Grep", "Edit"] };
    const payload = buildStep2Payload(live, edited);
    expect(payload.pool_size).toBe(8);
    expect(payload.base_tools).toEqual(["Read", "Grep", "Edit"]);
  });

  it("stepping back after a step-4 posture raise still carries the raised posture through, never resetting to observe", () => {
    const raisedLive = makeConfiguredEngineConfig({ posture: "propose" });
    const draft = seedDraftFromLive(raisedLive);
    const payload = buildStep2Payload(raisedLive, draft);
    expect(payload.posture).toBe("propose");
  });
});

describe("buildStep2Payload — a full replace must never omit or blank a field the read supplied", () => {
  const FULL_WIRE_LIVE = makeConfiguredEngineConfig({
    posture: "self_merge",
    hidden: true,
    hidden_restore_posture: "propose",
    accent: { color: "plum", intensity: "bold" },
    prompt_overrides: { "implementation/implementer": { text: "pinned", base_fingerprint: "fp" } },
    dependency_sweep: { enabled: true, cadence_seconds: 3600, enable_python_vuln_scan: true },
    unblock_enabled: false,
    cache_env: { UV_CACHE_DIR: "uv" },
    scenario: { command_prefixes: [["uv", "run"]], timeout_s: 30, aggregate_budget_s: 90 },
    code_quality: { project_key: "k", organization: "o", host_url: "h", token_env_var: "t" },
    pool_size: 4,
    flow_policy: proposedConfig().flow_policy,
  });

  const UNDECLARED_BY_THE_OLD_TYPE = [
    "hidden",
    "hidden_restore_posture",
    "accent",
    "unblock_enabled",
    "scenario",
    "cache_env",
  ];

  function partialWire(live: WireEngineConfig): EngineConfig {
    return Object.fromEntries(
      Object.entries(live).filter(([key]) => !UNDECLARED_BY_THE_OLD_TYPE.includes(key)),
    ) as unknown as EngineConfig;
  }

  it("keeps every key of a full-wire live engine on the payload built from live", () => {
    expectFullWireParity(buildStep2Payload(FULL_WIRE_LIVE, seedDraftFromLive(FULL_WIRE_LIVE)), FULL_WIRE_LIVE);
  });

  it("keeps every key of a full-wire live engine on the payload built from a proposal", () => {
    const payload = buildStep2Payload(FULL_WIRE_LIVE, seedDraft(makeProposal(), FULL_WIRE_LIVE));
    expectFullWireParity(payload, FULL_WIRE_LIVE);
  });

  it("keeps the key set of a partial-wire live engine without inventing the keys it lacked", () => {
    const partial = partialWire(FULL_WIRE_LIVE);
    expectFullWireParity(buildStep2Payload(partial, seedDraftFromLive(partial)), partial);
  });

  it("keeps the key set when the draft carries an edit", () => {
    const edited: Step2Draft = { ...seedDraftFromLive(FULL_WIRE_LIVE), pool_size: 8 };
    expectFullWireParity(buildStep2Payload(FULL_WIRE_LIVE, edited), FULL_WIRE_LIVE);
  });

  it.each(PROJECTION_EXCLUDED_FIELDS)("carries %s at live's non-default value through a proposal-seeded draft", (field) => {
    const payload = buildStep2Payload(FULL_WIRE_LIVE, seedDraft(makeProposal(), FULL_WIRE_LIVE));
    expect(payload[field]).toEqual(FULL_WIRE_LIVE[field]);
  });

  it.each(PROJECTION_EXCLUDED_FIELDS)("carries %s at live's non-default value through a live-seeded draft", (field) => {
    const payload = buildStep2Payload(FULL_WIRE_LIVE, seedDraftFromLive(FULL_WIRE_LIVE));
    expect(payload[field]).toEqual(FULL_WIRE_LIVE[field]);
  });

  it("shows a key holding undefined overwriting live's value and then vanishing from the serialised payload", () => {
    const draft = { ...seedDraftFromLive(FULL_WIRE_LIVE), gate_commands: undefined } as unknown as Step2Draft;
    const payload = buildStep2Payload(FULL_WIRE_LIVE, draft);
    expect(payload.gate_commands).toBeUndefined();
    expect(JSON.parse(JSON.stringify(payload))).not.toHaveProperty("gate_commands");
  });

  it("fails the parity helper for a draft key holding undefined", () => {
    const draft = { ...seedDraftFromLive(FULL_WIRE_LIVE), gate_commands: undefined } as unknown as Step2Draft;
    expect(() => {
      expectFullWireParity(buildStep2Payload(FULL_WIRE_LIVE, draft), FULL_WIRE_LIVE);
    }).toThrow();
  });

  it("fails the parity helper for undefined spread over the live hidden flag", () => {
    expect(() => {
      expectFullWireParity({ ...FULL_WIRE_LIVE, hidden: undefined }, FULL_WIRE_LIVE);
    }).toThrow();
  });

  it("fails the parity helper for undefined nested under the live dependency sweep", () => {
    const sweep = { ...FULL_WIRE_LIVE.dependency_sweep, enabled: undefined };
    expect(() => {
      expectFullWireParity({ ...FULL_WIRE_LIVE, dependency_sweep: sweep }, FULL_WIRE_LIVE);
    }).toThrow();
  });

  it("fails the parity helper for a payload that dropped keys live carried", () => {
    expect(() => {
      expectFullWireParity(partialWire(FULL_WIRE_LIVE), FULL_WIRE_LIVE);
    }).toThrow();
  });
});

describe("toDiffRows", () => {
  it("returns every row from proposal.diff, unfiltered — including unchanged fields", () => {
    const proposal = makeProposal();
    expect(toDiffRows(proposal)).toBe(proposal.diff);
    expect(toDiffRows(proposal).some((row) => !row.changed)).toBe(true);
  });
});

describe("step2 cache", () => {
  const engineKey = "acme/widget";

  beforeEach(() => {
    invalidateStep2Cache(engineKey);
  });

  it("is empty until written", () => {
    expect(readStep2Cache(engineKey)).toBeUndefined();
  });

  it("round-trips a written entry, keyed by engineKey", () => {
    const proposal = makeProposal();
    const draft = seedDraft(proposal, makeConfiguredEngineConfig());
    writeStep2Cache(engineKey, { proposal, draft });
    expect(readStep2Cache(engineKey)).toEqual({ proposal, draft });
    expect(readStep2Cache("other/repo")).toBeUndefined();
  });

  it("invalidate clears a previously written entry", () => {
    const proposal = makeProposal();
    writeStep2Cache(engineKey, { proposal, draft: seedDraft(proposal, makeConfiguredEngineConfig()) });
    invalidateStep2Cache(engineKey);
    expect(readStep2Cache(engineKey)).toBeUndefined();
  });
});

describe("fallbackFindingOf", () => {
  it("recovers the finding from a ConfigProposalBlockedError", () => {
    const finding = {
      check: "config_proposal",
      status: "fail" as const,
      message: "a config-proposal pipeline is already running for 'acme/widget'",
      remedy: "seed the engine configuration from a stack preset manually in the engine configuration editor",
      remedy_url: "",
    };
    expect(fallbackFindingOf(new ConfigProposalBlockedError(finding))).toEqual(finding);
  });

  it("returns null for any other error shape, never fabricating a finding", () => {
    expect(fallbackFindingOf(new ApiError(500, "boom"))).toBeNull();
    expect(fallbackFindingOf(new Error("network down"))).toBeNull();
    expect(fallbackFindingOf("raw string throw")).toBeNull();
  });
});

describe("parseStep2PoolSizeInput", () => {
  it("treats a blank string as null (default pool size)", () => {
    expect(parseStep2PoolSizeInput("")).toBeNull();
    expect(parseStep2PoolSizeInput("   ")).toBeNull();
  });

  it("parses a non-negative integer", () => {
    expect(parseStep2PoolSizeInput("4")).toBe(4);
    expect(parseStep2PoolSizeInput("0")).toBe(0);
  });

  it("rejects negative numbers, decimals, and non-numeric text as invalid", () => {
    expect(parseStep2PoolSizeInput("-1")).toBe("invalid");
    expect(parseStep2PoolSizeInput("1.5")).toBe("invalid");
    expect(parseStep2PoolSizeInput("abc")).toBe("invalid");
  });
});
