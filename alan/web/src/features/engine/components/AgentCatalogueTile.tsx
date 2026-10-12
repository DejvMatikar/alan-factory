import type { AgentCatalogueSummary } from "../../../api";
import Button from "../../../components/Button";
import SectionHeading from "../../../ui/SectionHeading";
import { useAgentCatalogueSummary } from "../hooks/useAgentCatalogue";

function plural(count: number, noun: string): string {
  return `${String(count)} ${noun}${count === 1 ? "" : "s"}`;
}

function modelSummary(summary: AgentCatalogueSummary): string {
  const models = summary.by_model.map((entry) => `${String(entry.count)} on ${entry.name}`);
  const total = plural(summary.total - summary.errors, "agent");
  return models.length === 0 ? total : `${total}: ${models.join(", ")}`;
}

function flagSummary(summary: AgentCatalogueSummary): string | null {
  const parts = [
    summary.warnings > 0 ? plural(summary.warnings, "warning") : null,
    summary.infos > 0 ? plural(summary.infos, "note") : null,
    summary.errors > 0 ? `${plural(summary.errors, "agent")} could not be resolved` : null,
  ].filter((part) => part !== null);
  return parts.length === 0 ? null : `Flagged: ${parts.join(", ")}`;
}

function TileBody({ engineKey }: Readonly<{ engineKey: string }>) {
  const state = useAgentCatalogueSummary(engineKey);
  if (state.status === "loading") {
    return (
      <p className="text-sm text-text-muted" data-testid="agent-catalogue-tile-loading">
        Loading…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <p role="alert" className="text-sm text-btn-destroy" data-testid="agent-catalogue-tile-error">
          {state.message}
        </p>
        <Button variant="ghost" onClick={state.refetch} data-testid="agent-catalogue-tile-retry">
          Retry
        </Button>
      </div>
    );
  }
  const flags = flagSummary(state.data);
  return (
    <div className="space-y-1 text-sm text-text-primary">
      <p data-testid="agent-catalogue-tile-models">{modelSummary(state.data)}</p>
      {flags !== null && (
        <p className="text-amber-300" data-testid="agent-catalogue-tile-flags">
          {flags}
        </p>
      )}
    </div>
  );
}

export default function AgentCatalogueTile({ engineKey }: Readonly<{ engineKey: string }>) {
  return (
    <section
      className="space-y-3 rounded-lg border border-border bg-surface-elevated px-4 py-3"
      data-testid="agent-catalogue-tile"
    >
      <SectionHeading level="h4">Agents</SectionHeading>
      <TileBody engineKey={engineKey} />
      <Button variant="action" linkTo={`/repos/${engineKey}/agents`} data-testid="agent-catalogue-tile-link">
        Open the agent catalogue
      </Button>
    </section>
  );
}
