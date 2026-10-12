import { useCallback, useState } from "react";

import { api } from "../../../api";
import type { BudgetState, ProposalItem } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import { useChangeRefetch } from "../../../api/ServerEventsProvider";

export interface UseNextItemProposalResult {
  proposal: ProposalItem | null;
  budgetState: BudgetState | null;
  loaded: boolean;
  error: boolean;
  advancing: boolean;
  advanceError: string | null;
  advance: () => void;
}

export function useNextItemProposal(
  projectId: string,
  enabled: boolean,
): UseNextItemProposalResult {
  const [proposal, setProposal] = useState<ProposalItem | null>(null);
  const [budgetState, setBudgetState] = useState<BudgetState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [advancing, setAdvancing] = useState(false);
  const [advanceError, setAdvanceError] = useState<string | null>(null);

  const fetchProposal = useCallback(() => {
    api.getRoadmapNextItemProposal(projectId).then(
      (resp) => {
        setProposal(resp.proposal);
        setBudgetState(resp.budget_state);
        setLoaded(true);
        setError(false);
      },
      () => {
        setLoaded(true);
        setError(true);
      },
    );
  }, [projectId]);

  useChangeRefetch("workspace_list", fetchProposal, enabled);

  const advance = useCallback(() => {
    setAdvancing(true);
    setAdvanceError(null);
    api.advanceProjectRoadmap(projectId).then(
      () => {
        setAdvancing(false);
        fetchProposal();
      },
      (err: unknown) => {
        setAdvancing(false);
        setAdvanceError(errorMessageOf(err));
        fetchProposal();
      },
    );
  }, [projectId, fetchProposal]);

  return { proposal, budgetState, loaded, error, advancing, advanceError, advance };
}
