import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type CounterCase, type PrepChatKind } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import { useAction } from "../../../ui/useAction";

export interface DispatchWorkspaceOptions {
  acceptanceClauses?: string[] | undefined;
  recordedClauses?: string[] | undefined;
  counterCase?: CounterCase | undefined;
  instructions?: string | null | undefined;
  projectId?: string | null | undefined;
  roadmapItemId?: string | null | undefined;
  engineKey?: string | null | undefined;
  prepChatSessionId?: string | null | undefined;
}

// The live prep-chat session is archived server-side as part of the fused
// create-and-dispatch call (prep_chat_kind/prep_chat_session_id below) — the
// draft is never deleted from here; dispatch converts it into a workspace
// artifact instead.
export function useDispatchWorkspace(team: string, prepChatKind: PrepChatKind) {
  const navigate = useNavigate();
  const { busy: dispatching, run } = useAction();
  const [dispatchError, setDispatchError] = useState<string | null>(null);

  const dispatchWorkspace = useCallback(
    async (
      title: string,
      description: string,
      options: DispatchWorkspaceOptions = {},
    ): Promise<boolean> => {
      const {
        acceptanceClauses,
        recordedClauses,
        counterCase,
        instructions,
        projectId,
        roadmapItemId,
        engineKey,
        prepChatSessionId,
      } = options;
      setDispatchError(null);
      return run(async () => {
        try {
          await api.createAndDispatchWorkspace(
            title,
            description,
            team,
            { counter_case: counterCase, instructions },
            {
              acceptanceClauses,
              recordedClauses,
              projectId,
              roadmapItemId,
              engineKey,
              prepChatKind: prepChatSessionId != null ? prepChatKind : undefined,
              prepChatSessionId,
            },
          );
          await navigate("/");
          return true;
        } catch (err: unknown) {
          setDispatchError(errorMessageOf(err));
          return false;
        }
      });
    },
    [navigate, team, prepChatKind, run],
  );

  return { dispatchWorkspace, dispatching, dispatchError };
}
