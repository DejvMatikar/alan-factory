import { HttpResponse, http } from "msw";
import { expect } from "vitest";

import type { EngineConfig, EngineSummary } from "../api";
import { queryClient } from "../api/queryClient";

export type WireEngineConfig = Required<EngineConfig>;

export const PROJECTION_EXCLUDED_FIELDS = [
  "posture",
  "accent",
  "hidden",
  "hidden_restore_posture",
  "prompt_overrides",
  "dependency_sweep",
] as const;

export function makeEngineConfig(overrides: Partial<EngineConfig> = {}): WireEngineConfig {
  return {
    repo: { owner: "acme", repo: "widget" },
    code_quality: null,
    forge: null,
    ci: null,
    roles: {},
    gate_commands: [],
    warmup_commands: [],
    cache_env: {},
    base_tools: [],
    team_tools: {},
    transition_graph: {},
    flow_policy: null,
    posture: "observe",
    hidden: false,
    hidden_restore_posture: null,
    pool_size: null,
    preset: null,
    prompt_overrides: {},
    dependency_sweep: { enabled: false, cadence_seconds: 604800, enable_python_vuln_scan: false },
    accent: null,
    scenario: { command_prefixes: [], timeout_s: null, aggregate_budget_s: null },
    unblock_enabled: true,
    ...overrides,
  };
}

export function forgeTargetFor(repo: { owner: string; repo: string }): NonNullable<EngineConfig["forge"]> {
  return {
    repo,
    credential_env_name: "ACME_WIDGET_GH_TOKEN",
    authoritative_checks: [],
    enforce_admins: false,
  };
}

export function makeConfiguredEngineConfig(overrides: Partial<EngineConfig> = {}): WireEngineConfig {
  return makeEngineConfig({
    forge: forgeTargetFor({ owner: "acme", repo: "widget" }),
    roles: { implementer: { model: "sonnet", effort: "high" } },
    gate_commands: [["uv", "run", "pytest"]],
    warmup_commands: [["uv", "sync"]],
    base_tools: ["Read"],
    team_tools: { implementation: ["Edit"] },
    transition_graph: { research: ["architecture"], architecture: [] },
    preset: "python",
    ...overrides,
  });
}

export function makeEngineSummary(overrides: Partial<EngineSummary> = {}): EngineSummary {
  return { repo: { owner: "acme", repo: "widget" }, posture: "observe", hidden: false, ...overrides };
}

export const STUB_DEFAULT_ENGINE_KEY = "DejvMatikar/alan";

export const LISTED_DEFAULT_KEY = "acme/listed-default";

export type DefaultKeyState = "equal" | "other";

export const DEFAULT_KEY_STATES: readonly (readonly [DefaultKeyState, string])[] = [
  ["equal", STUB_DEFAULT_ENGINE_KEY],
  ["other", LISTED_DEFAULT_KEY],
];

export function settleDefaultEngineKey(key: string | null): void {
  queryClient.setQueryData(["engines", "default"], key);
}

export interface EngineListingStub {
  readonly engines: readonly EngineSummary[];
  readonly defaultKey?: string | undefined;
}

function listingBody({ engines, defaultKey }: Readonly<EngineListingStub>) {
  return defaultKey === undefined ? { engines } : { engines, default_engine_key: defaultKey };
}

export function engineListingHandler(stub: Readonly<EngineListingStub>) {
  return http.get("/api/engines", () => HttpResponse.json(listingBody(stub)));
}

export function gatedEngineListingHandler(stub: Readonly<EngineListingStub>) {
  let open: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  const handler = http.get("/api/engines", async () => {
    await gate;
    return HttpResponse.json(listingBody(stub));
  });
  return { handler, release: () => open() };
}

export function makeEngineSummaryFor(
  key: string,
  overrides: Partial<EngineSummary> = {},
): EngineSummary {
  const [owner = "", repo = ""] = key.split("/");
  return makeEngineSummary({ repo: { owner, repo }, ...overrides });
}

export function makeConfiguredEngineSummaryFor(
  key: string,
  overrides: Partial<EngineSummary> = {},
): EngineSummary {
  const summary = makeEngineSummaryFor(key);
  return { ...summary, forge: forgeTargetFor(summary.repo), ...overrides };
}

function expectNoUndefinedDeep(value: unknown, path: string): void {
  expect(value, `${path} must not be undefined`).not.toBeUndefined();
  if (typeof value === "object" && value !== null) {
    for (const [key, child] of Object.entries(value)) {
      expectNoUndefinedDeep(child, `${path}.${key}`);
    }
  }
}

export function expectFullWireParity(payload: object, live: object): void {
  const wire = JSON.parse(JSON.stringify(payload)) as object;
  expect(Object.keys(wire).sort()).toEqual(Object.keys(live).sort());
  expectNoUndefinedDeep(payload, "payload");
}
