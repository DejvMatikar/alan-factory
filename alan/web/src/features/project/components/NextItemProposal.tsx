import type { BudgetState, ProjectAutonomy, ProposalItem } from "../../../api";
import Button from "../../../components/Button";
import { useNextItemProposal } from "../hooks/useNextItemProposal";

export interface NextItemProposalProps {
  projectId: string;
  autonomy: ProjectAutonomy;
  roadmapDelivered: number;
  roadmapTotal: number;
}

function NothingLeftToStart() {
  return (
    <p className="text-sm text-text-muted" data-testid="next-item-proposal-none">
      Every roadmap item has delivered — nothing left to start.
    </p>
  );
}

function emptyState(
  budgetState: BudgetState | null,
  roadmapDelivered: number,
  roadmapTotal: number,
) {
  if (budgetState === "paused") return null;
  if (roadmapTotal > 0 && roadmapDelivered === roadmapTotal) return <NothingLeftToStart />;
  return null;
}

interface ProposalCardProps {
  proposal: ProposalItem;
  advancing: boolean;
  advanceError: string | null;
  advance: () => void;
}

function ProposalCard({ proposal, advancing, advanceError, advance }: Readonly<ProposalCardProps>) {
  const startLabel = proposal.actions[0]?.label ?? `Start: ${proposal.title}`;
  return (
    <div
      className="space-y-2 rounded-md border border-border bg-surface-elevated p-3"
      data-testid="next-item-proposal"
    >
      <p className="text-sm font-medium text-text-primary" data-testid="next-item-proposal-title">
        {proposal.title}
      </p>
      <p className="text-sm text-text-muted">{proposal.recommendation}</p>
      <Button
        variant="create"
        data-testid="next-item-proposal-start"
        onClick={advance}
        disabled={advancing}
      >
        {advancing ? "Starting…" : startLabel}
      </Button>
      {advanceError !== null && (
        <p
          role="alert"
          data-testid="next-item-proposal-advance-error"
          className="text-sm text-btn-destroy"
        >
          {advanceError}
        </p>
      )}
    </div>
  );
}

export default function NextItemProposal({
  projectId,
  autonomy,
  roadmapDelivered,
  roadmapTotal,
}: Readonly<NextItemProposalProps>) {
  const enabled = autonomy === "propose";
  const { proposal, budgetState, loaded, error, advancing, advanceError, advance } =
    useNextItemProposal(projectId, enabled);

  if (!enabled) return null;

  if (!loaded) {
    return (
      <p className="text-sm text-text-muted" data-testid="next-item-proposal-loading">
        Checking for a next roadmap item…
      </p>
    );
  }

  if (error) {
    return (
      <p
        role="alert"
        className="text-sm text-btn-destroy"
        data-testid="next-item-proposal-error"
      >
        Unable to load the next roadmap item recommendation.
      </p>
    );
  }

  if (proposal === null) {
    return emptyState(budgetState, roadmapDelivered, roadmapTotal);
  }

  return (
    <ProposalCard
      proposal={proposal}
      advancing={advancing}
      advanceError={advanceError}
      advance={advance}
    />
  );
}
