import type {
  EngineFlowGraphPolicy,
  EngineRoleConfig,
  ProposableEngineConfigField,
} from "../../../api";

export const FIELD_LABEL: Record<ProposableEngineConfigField, string> = {
  roles: "Roles",
  gate_commands: "Gate commands",
  warmup_commands: "Warmup commands",
  base_tools: "Base tools",
  team_tools: "Per-team tool allowlists",
  transition_graph: "Team transition graph",
  flow_policy: "Flow policy",
};

// The slug every proposal-field testId derives from — shared so the panel,
// the field editor, and the diff table build identical ids for the same
// field instead of three independent replaceAll("_", "-") call sites.
export function fieldSlug(field: ProposableEngineConfigField): string {
  return field.replaceAll("_", "-");
}

function flowPolicySummary(policy: EngineFlowGraphPolicy | null): string {
  if (policy === null) return "No flow policy";
  const parts = [
    `verifier: ${policy.verifier_teams.join(", ") || "—"}`,
    `merge: ${policy.merge_teams.join(", ") || "—"}`,
    `terminal: ${policy.terminal_teams.join(", ") || "—"}`,
    `internal-only: ${policy.internal_only_teams.join(", ") || "—"}`,
    `revisit cap: ${String(policy.revisit_cap)}`,
  ];
  return parts.join(" · ");
}

function summarizeCommands(value: string[][]): string {
  return value.map((command) => command.join(" ")).join(" | ") || "—";
}

function summarizeList(value: string[]): string {
  return value.join(", ") || "—";
}

function summarizeKeyedList(value: Record<string, string[]>): string {
  const entries = Object.entries(value).sort(([a], [b]) => a.localeCompare(b));
  return entries.map(([k, v]) => `${k}: ${v.join(", ") || "—"}`).join("; ") || "—";
}

function summarizeRole(role: EngineRoleConfig): string {
  return role.effort === null ? role.model : `${role.model}/${role.effort}`;
}

function summarizeRoles(value: Record<string, EngineRoleConfig>): string {
  const entries = Object.entries(value).sort(([a], [b]) => a.localeCompare(b));
  return entries.map(([k, v]) => `${k}: ${summarizeRole(v)}`).join("; ") || "—";
}

const FIELD_SUMMARIZER: Record<ProposableEngineConfigField, (value: unknown) => string> = {
  gate_commands: (value) => summarizeCommands(value as string[][]),
  warmup_commands: (value) => summarizeCommands(value as string[][]),
  base_tools: (value) => summarizeList(value as string[]),
  team_tools: (value) => summarizeKeyedList(value as Record<string, string[]>),
  transition_graph: (value) => summarizeKeyedList(value as Record<string, string[]>),
  roles: (value) => summarizeRoles(value as Record<string, EngineRoleConfig>),
  flow_policy: (value) => flowPolicySummary(value as EngineFlowGraphPolicy | null),
};

export function summarizeFieldValue(field: ProposableEngineConfigField, value: unknown): string {
  return FIELD_SUMMARIZER[field](value);
}
