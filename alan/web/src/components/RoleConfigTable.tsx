import type { EngineRoleConfig } from "../api";
import Input from "../ui/Input";
import SectionHeading from "../ui/SectionHeading";

export interface RoleConfigTableProps {
  testId: string;
  value: Readonly<Record<string, EngineRoleConfig>>;
  onChange: (next: Record<string, EngineRoleConfig>) => void;
  disabled?: boolean;
}

const MODEL_OPTIONS = ["sonnet", "opus", "haiku"];

// Roles are whatever the loaded config already declares — no add/remove-role
// affordance, only each role's model/effort is editable. Effort is free text
// so any future effort tier round-trips even though only a fixed set is
// datalist-suggested; an empty effort field means None, not "unset".
export default function RoleConfigTable({
  testId,
  value,
  onChange,
  disabled = false,
}: Readonly<RoleConfigTableProps>) {
  const rows = Object.entries(value).sort(([a], [b]) => a.localeCompare(b));
  const modelListId = `${testId}-model-options`;

  const handleModelChange = (role: string, cfg: EngineRoleConfig, model: string): void => {
    onChange({ ...value, [role]: { ...cfg, model } });
  };

  const handleEffortChange = (role: string, cfg: EngineRoleConfig, effortText: string): void => {
    const effort = effortText.trim() === "" ? null : effortText;
    onChange({ ...value, [role]: { ...cfg, effort } });
  };

  return (
    <div className="space-y-2" data-testid={testId}>
      <SectionHeading level="h4">Roles</SectionHeading>
      {rows.length === 0 && (
        <p className="text-sm text-text-muted" data-testid={`${testId}-empty`}>
          No roles.
        </p>
      )}
      {rows.length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th scope="col" className="text-left text-xs font-medium text-text-muted">
                Role
              </th>
              <th scope="col" className="text-left text-xs font-medium text-text-muted">
                Model
              </th>
              <th scope="col" className="text-left text-xs font-medium text-text-muted">
                Effort
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([role, cfg]) => {
              const modelId = `${testId}-model-${role}`;
              const effortId = `${testId}-effort-${role}`;
              return (
                <tr key={role}>
                  <th scope="row" className="py-1 pr-2 text-left font-normal text-text-primary">
                    {role}
                  </th>
                  <td className="py-1 pr-2">
                    <label className="sr-only" htmlFor={modelId}>
                      {role} model
                    </label>
                    <Input
                      as="input"
                      id={modelId}
                      data-testid={modelId}
                      type="text"
                      list={modelListId}
                      value={cfg.model}
                      onChange={(e) => {
                        handleModelChange(role, cfg, e.target.value);
                      }}
                      disabled={disabled}
                    />
                  </td>
                  <td className="py-1">
                    <label className="sr-only" htmlFor={effortId}>
                      {role} effort
                    </label>
                    <Input
                      as="input"
                      id={effortId}
                      data-testid={effortId}
                      type="text"
                      value={cfg.effort ?? ""}
                      onChange={(e) => {
                        handleEffortChange(role, cfg, e.target.value);
                      }}
                      disabled={disabled}
                      placeholder="None"
                      placeholderStyle
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <datalist id={modelListId}>
        {MODEL_OPTIONS.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>
    </div>
  );
}
