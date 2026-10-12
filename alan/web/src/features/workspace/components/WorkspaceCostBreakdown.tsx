import type { WorkspaceCostBreakdownEntry } from "../../../api";
import RunPricing, { type RunPricingEntry } from "../../../components/RunPricing";
import Disclosure from "../../../ui/Disclosure";

function toRunPricingEntry(entry: WorkspaceCostBreakdownEntry): RunPricingEntry {
  const cost = entry.run_summary?.total_cost_usd ?? null;
  return {
    key: entry.key,
    runSummary:
      cost === null
        ? null
        : {
            model: entry.label,
            effort: null,
            system_prompt: "",
            total_cost_usd: cost,
            total_output_tokens: null,
            total_input_tokens: null,
            tool_counts: [],
          },
  };
}

export interface WorkspaceCostBreakdownProps {
  entries: readonly WorkspaceCostBreakdownEntry[];
}

export default function WorkspaceCostBreakdown({
  entries,
}: Readonly<WorkspaceCostBreakdownProps>) {
  const runs = entries.map(toRunPricingEntry);
  if (runs.every((run) => run.runSummary === null)) return null;

  return (
    <Disclosure
      preset="panelXs"
      defaultOpen={false}
      testId="workspace-cost-breakdown"
      label={<span>Cost breakdown</span>}
    >
      <div className="mt-2">
        <RunPricing runs={runs} showSystemPrompt={false} showTokens={false} />
      </div>
    </Disclosure>
  );
}
