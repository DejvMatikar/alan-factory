import {
  ConfigProposalBlockedError,
  type ConfigProposalResult,
  type EngineConfig,
  type EnginePreflightFinding,
  type ProposableEngineConfigField,
} from "../../../api";

// Posture is deliberately NOT part of this Pick — see buildStep2Payload
// below. code_quality/pool_size ride along because they render as editable
// baseline fields on this step even though the proposal pipeline never
// touches them (they are excluded from _PROPOSABLE_FIELDS on the backend).
export type Step2Draft = Pick<EngineConfig, ProposableEngineConfigField | "code_quality" | "pool_size">;

export function seedDraft(proposal: ConfigProposalResult, live: EngineConfig): Step2Draft {
  return {
    gate_commands: proposal.proposed_config.gate_commands!,
    warmup_commands: proposal.proposed_config.warmup_commands!,
    base_tools: proposal.proposed_config.base_tools!,
    team_tools: proposal.proposed_config.team_tools!,
    roles: proposal.proposed_config.roles!,
    transition_graph: proposal.proposed_config.transition_graph!,
    flow_policy: proposal.proposed_config.flow_policy ?? null,
    code_quality: live.code_quality ?? null,
    pool_size: live.pool_size ?? null,
  };
}

// Used when no proposal is available (the 422 fallback) — every field
// starts at the engine's own currently-persisted value, edited manually or
// via EnginePresetSeedButton's transition_graph/flow_policy overlay.
export function seedDraftFromLive(live: EngineConfig): Step2Draft {
  return {
    gate_commands: live.gate_commands!,
    warmup_commands: live.warmup_commands!,
    base_tools: live.base_tools!,
    team_tools: live.team_tools!,
    roles: live.roles!,
    transition_graph: live.transition_graph!,
    flow_policy: live.flow_policy ?? null,
    code_quality: live.code_quality ?? null,
    pool_size: live.pool_size ?? null,
  };
}

// The client-side half of "posture no-op by construction": draft can never
// carry a posture key (see the Pick above), so spreading it over live can
// never clobber live's posture, forge, ci, repo, preset, or prompt_overrides
// — every field this step doesn't render rides through unchanged. This is
// also why save is always an update, never a create: live always comes from
// a GET against the engine step 1 already registered.
export function buildStep2Payload(live: EngineConfig, draft: Step2Draft): EngineConfig {
  return { ...live, ...draft };
}

// Identity over proposal.diff — deliberately no `changed` filter. _field_diff
// already emits exactly the seven proposable fields, sorted, regardless of
// whether the proposed value differs from baseline, so the diff table's row
// set comes free from this alone.
export function toDiffRows(proposal: ConfigProposalResult): ConfigProposalResult["diff"] {
  return proposal.diff;
}

export interface Step2CacheEntry {
  proposal: ConfigProposalResult;
  draft: Step2Draft;
}

// Module-scope, in-memory only — survives navigation within the wizard (SPA
// routing never reloads this module) but dies on a real page reload, which
// is exactly the re-entry contract this step accepted: a cold load always
// re-derives from server reads, never from client storage.
const cache = new Map<string, Step2CacheEntry>();

export function readStep2Cache(engineKey: string): Step2CacheEntry | undefined {
  return cache.get(engineKey);
}

export function writeStep2Cache(engineKey: string, entry: Step2CacheEntry): void {
  cache.set(engineKey, entry);
}

export function invalidateStep2Cache(engineKey: string): void {
  cache.delete(engineKey);
}

// _manual_seed_finding wraps both ProposalAbortedError and
// ProposalUnrepairableError into the same Finding shape — this only recovers
// the structured finding for that one documented 422 case; any other
// rejection (network error, an unrelated ApiError) returns null so the
// caller renders a generic error instead of fabricating a cause.
export function fallbackFindingOf(err: unknown): EnginePreflightFinding | null {
  return err instanceof ConfigProposalBlockedError ? err.finding : null;
}

export function parseStep2PoolSizeInput(text: string): number | null | "invalid" {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return "invalid";
  return Number(trimmed);
}
