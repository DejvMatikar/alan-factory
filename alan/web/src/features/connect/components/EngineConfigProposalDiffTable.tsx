import type { EngineConfig, ProposableEngineConfigField, ProposalFieldDiff } from "../../../api";
import { FIELD_LABEL, fieldSlug, summarizeFieldValue } from "./engineConfigFieldSummary";
import EngineConfigFieldEditor from "../../../components/EngineConfigFieldEditor";

export interface EngineConfigProposalDiffTableProps {
  diff: readonly ProposalFieldDiff[];
  values: Pick<EngineConfig, ProposableEngineConfigField>;
  disabled: boolean;
  onFieldChange: (field: ProposableEngineConfigField, value: unknown) => void;
}

// The design system's first real data table (rule 9) — chrome matches
// RoleConfigTable.tsx / Home's RepoSection.tsx rather than inventing a third
// convention. One row per ProposalFieldDiff, unfiltered by `changed` (see
// toDiffRows in connectStep2ConfigureLogic.ts) — the row set is exactly
// _PROPOSABLE_FIELDS, sorted, by construction. "Now" is a column label only;
// the model's own fields stay baseline/proposed/changed. Every "Now" cell is
// a focusable editor — approving and amending are one surface, with no
// separate accept/edit/reject decision model.
export default function EngineConfigProposalDiffTable({
  diff,
  values,
  disabled,
  onFieldChange,
}: Readonly<EngineConfigProposalDiffTableProps>) {
  return (
    <table className="w-full text-left text-sm" data-testid="connect-step2-diff-table">
      <thead className="border-b border-border text-xs uppercase text-text-muted">
        <tr>
          <th scope="col" className="py-2 pr-4 font-medium">
            Field
          </th>
          <th scope="col" className="py-2 pr-4 font-medium">
            Baseline
          </th>
          <th scope="col" className="py-2 font-medium">
            Now
          </th>
        </tr>
      </thead>
      <tbody>
        {diff.map((row) => {
          const rowTestId = `connect-step2-diff-${fieldSlug(row.field)}`;
          return (
            <tr key={row.field} className="border-b border-border/50" data-testid={rowTestId}>
              <th scope="row" className="py-2 pr-4 align-top font-normal text-text-primary">
                {FIELD_LABEL[row.field]}
              </th>
              <td className="py-2 pr-4 align-top text-text-muted" data-testid={`${rowTestId}-baseline`}>
                {summarizeFieldValue(row.field, row.baseline)}
              </td>
              <td className="py-2 align-top" data-testid={`${rowTestId}-now`}>
                <EngineConfigFieldEditor
                  field={row.field}
                  value={values[row.field]}
                  disabled={disabled}
                  onChange={(value) => {
                    onFieldChange(row.field, value);
                  }}
                  testId={`${rowTestId}-now-editor`}
                />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
