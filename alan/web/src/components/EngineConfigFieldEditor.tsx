import type {
  EngineFlowGraphPolicy,
  EngineRoleConfig,
  ProposableEngineConfigField,
} from "../api";
import Input from "../ui/Input";
import ArgvListEditor from "./ArgvListEditor";
import KeyedListDictEditor from "./KeyedListDictEditor";
import RoleConfigTable from "./RoleConfigTable";

export interface EngineConfigFieldEditorProps {
  field: ProposableEngineConfigField;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
  testId: string;
}

const FLOW_POLICY_TEAM_FIELDS = [
  "verifier_teams",
  "merge_teams",
  "terminal_teams",
  "internal_only_teams",
] as const;

function flowPolicyTeamsRecord(policy: EngineFlowGraphPolicy): Record<string, string[]> {
  const record: Record<string, string[]> = {};
  for (const key of FLOW_POLICY_TEAM_FIELDS) record[key] = policy[key];
  return record;
}

// revisit_cap is a non-negative integer in the backend model — reject
// negatives, decimals, and blank input rather than coercing them (Number("")
// is 0, Number("-1")/Number("1.5") are non-NaN), matching
// parseStep2PoolSizeInput's ignore-invalid-input contract.
function parseRevisitCapInput(text: string): number | "invalid" {
  const trimmed = text.trim();
  return /^\d+$/.test(trimmed) ? Number(trimmed) : "invalid";
}

interface FlowPolicyEditorProps {
  testId: string;
  value: EngineFlowGraphPolicy | null;
  disabled: boolean;
  onChange: (value: EngineFlowGraphPolicy) => void;
}

// flow_policy has no proposal-panel precedent for inline editing (the old
// accept/edit/reject model never offered "edit" for it) — this is new,
// necessary here because every proposed cell in the step-2 diff table must
// be a focusable control. restricted_predecessors is deliberately left out,
// matching flowPolicySummary's own scope decision in engineConfigFieldSummary.ts.
function FlowPolicyEditor({ testId, value, disabled, onChange }: Readonly<FlowPolicyEditorProps>) {
  if (value === null) {
    return (
      <p className="text-sm text-text-muted" data-testid={testId}>
        No flow policy to edit.
      </p>
    );
  }
  const revisitCapId = `${testId}-revisit-cap`;
  return (
    <div className="space-y-2" data-testid={testId}>
      <KeyedListDictEditor
        label="Flow policy teams"
        testId={`${testId}-teams`}
        value={flowPolicyTeamsRecord(value)}
        onChange={(next) => {
          // KeyedListDictEditor's onChange always returns an entry for every
          // key present in the value it was given — flowPolicyTeamsRecord
          // always supplies exactly these four, so each is always present.
          onChange({
            ...value,
            verifier_teams: next.verifier_teams!,
            merge_teams: next.merge_teams!,
            terminal_teams: next.terminal_teams!,
            internal_only_teams: next.internal_only_teams!,
          });
        }}
        disabled={disabled}
      />
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor={revisitCapId}>
          Revisit cap
        </label>
        <Input
          as="input"
          id={revisitCapId}
          data-testid={revisitCapId}
          type="text"
          inputMode="numeric"
          value={value.revisit_cap}
          onChange={(e) => {
            const parsed = parseRevisitCapInput(e.target.value);
            if (parsed !== "invalid") onChange({ ...value, revisit_cap: parsed });
          }}
          disabled={disabled}
          tapTarget
          focusRing
        />
      </div>
    </div>
  );
}

// One editor per ProposableEngineConfigField, reusing the shared list/table
// editors rather than hand-rolling a fourth per-field control shape.
export default function EngineConfigFieldEditor({
  field,
  value,
  disabled,
  onChange,
  testId,
}: Readonly<EngineConfigFieldEditorProps>) {
  switch (field) {
    case "roles":
      return (
        <RoleConfigTable
          testId={testId}
          value={value as Record<string, EngineRoleConfig>}
          onChange={onChange}
          disabled={disabled}
        />
      );
    case "gate_commands":
      return (
        <ArgvListEditor
          label="Gate commands"
          testId={testId}
          value={value as string[][]}
          onChange={onChange}
          disabled={disabled}
        />
      );
    case "warmup_commands":
      return (
        <ArgvListEditor
          label="Warmup commands"
          testId={testId}
          value={value as string[][]}
          onChange={onChange}
          disabled={disabled}
        />
      );
    case "team_tools":
      return (
        <KeyedListDictEditor
          label="Per-team tool allowlists"
          testId={testId}
          value={value as Record<string, string[]>}
          onChange={onChange}
          disabled={disabled}
        />
      );
    case "transition_graph":
      return (
        <KeyedListDictEditor
          label="Next teams"
          testId={testId}
          value={value as Record<string, string[]>}
          onChange={onChange}
          disabled={disabled}
        />
      );
    case "base_tools":
      return (
        <div className="space-y-1">
          <label className="text-xs font-medium text-text-muted" htmlFor={testId}>
            Base tools (one per line)
          </label>
          <Input
            as="textarea"
            id={testId}
            data-testid={testId}
            value={(value as string[]).join("\n")}
            onChange={(e) => {
              onChange(
                e.target.value
                  .split("\n")
                  .map((line) => line.trim())
                  .filter((line) => line !== ""),
              );
            }}
            disabled={disabled}
            rows={4}
            tapTarget
            focusRing
          />
        </div>
      );
    case "flow_policy":
      return (
        <FlowPolicyEditor
          testId={testId}
          value={value as EngineFlowGraphPolicy | null}
          disabled={disabled}
          onChange={onChange}
        />
      );
  }
}
