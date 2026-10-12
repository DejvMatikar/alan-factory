import { useMemo } from "react";

import { api, type ChatBrief } from "../../../api";
import { useChatSessionQuery } from "../../../api/queries";
import {
  useChatSessionGeneric,
  type ChatSessionApi,
  type UseGenericChatSessionResult,
} from "./useChatSessionGeneric";
import { useEngines } from "../../../hooks/useEngines";

export interface UseExploreChatSessionResult
  extends Omit<UseGenericChatSessionResult<ChatBrief>, "summarize" | "updateBrief"> {
  summarize: () => Promise<ChatBrief>;
  updateBrief: (content: string) => Promise<ChatBrief>;
  engineKey: string | null;
}

// undefined = no repo to inherit — either no session is bound yet, or the
// session's own fetch resolved without a repo of its own; "clear" mints a
// fresh default-repo session in that case. null = the binding fetch never
// succeeded — genuinely unresolved, the one case that must not silently
// default. A string is the session's own resolved engine_key.
type EngineKeyBinding = string | null | undefined;

// The generic chat hook's ChatSessionShape doesn't carry engine_key, so this hook
// reads the session's own binding from the shared chat-session query cache entry
// (family "explore") — both to carry it forward into "clear and start over"
// (createNew) and to thread it into the dispatch hand-off, mirroring
// useBuildChatSession's useSessionBinding.
function useEngineKeyBinding(sessionId: string | null): EngineKeyBinding {
  const { data, isError } = useChatSessionQuery("explore", sessionId, (id) =>
    api.getExploreChatSession(id),
  );
  // Only a never-successful read is a genuine, distinct unresolved state — a
  // resolved session with no repo of its own still falls through to the
  // default via the undefined branch, and a failed background refresh after
  // a prior success keeps the last known binding.
  return isError && data === undefined ? null : data?.engineKey;
}

function createFromEngineKeyBinding(
  consultantKey: string,
  engineKey: EngineKeyBinding,
  fallbackKey: string | null,
): Promise<{ session_id: string }> {
  if (engineKey === null) {
    return Promise.reject(new Error("repository unresolved for this session"));
  }
  const resolvedKey = engineKey ?? fallbackKey;
  if (resolvedKey === null) {
    return Promise.reject(new Error("default repository not loaded"));
  }
  return api.createExploreChatSession(consultantKey, resolvedKey);
}

export function useExploreChatSession(
  sessionId: string | null,
  consultantKey: string,
): UseExploreChatSessionResult {
  const engineKey = useEngineKeyBinding(sessionId);
  const { loaded, defaultEngineKey } = useEngines();
  const fallbackKey = loaded ? defaultEngineKey : null;
  const binding = useMemo<ChatSessionApi<ChatBrief>>(
    () => ({
      family: "explore",
      get: api.getExploreChatSession,
      subscribe: api.subscribeExploreChatSession,
      send: api.sendExploreChatMessage,
      interrupt: api.interruptExploreChatSession,
      summarize: api.summarizeExploreChat,
      putBrief: api.putExploreChatBrief,
      delete: api.deleteExploreChatSession,
      createNew: () => createFromEngineKeyBinding(consultantKey, engineKey, fallbackKey),
      noSessionError: "no explore-chat session",
    }),
    [consultantKey, engineKey, fallbackKey],
  );

  const base = useChatSessionGeneric<ChatBrief>(sessionId, binding);
  return {
    ...base,
    summarize: base.summarize!,
    updateBrief: base.updateBrief!,
    engineKey: engineKey ?? null,
  };
}
