import { useMemo } from "react";

import { api, type ChatBrief } from "../../../api";
import { useChatSessionQuery } from "../../../api/queries";
import {
  useChatSessionGeneric,
  type ChatSessionApi,
  type UseGenericChatSessionResult,
} from "./useChatSessionGeneric";
import { useEngines } from "../../../hooks/useEngines";

export interface UseBuildChatSessionResult
  extends Omit<UseGenericChatSessionResult<ChatBrief>, "summarize" | "updateBrief"> {
  summarize: () => Promise<ChatBrief>;
  updateBrief: (content: string) => Promise<ChatBrief>;
  projectId: string | null;
  engineKey: string | null;
}

interface Binding {
  projectId: string | null;
  // undefined = no repo to inherit — either no session is bound yet, or the
  // session's own fetch resolved without a repo of its own; "clear" mints a
  // fresh default-repo session in that case. null = the binding fetch itself
  // failed — genuinely unresolved, the one case that must not silently
  // default. A string is the session's own resolved engine_key.
  engineKey: string | null | undefined;
}

// The generic chat hook's ChatSessionShape doesn't carry project_id/engine_key
// (shared with explore chat, which has no project binding), so this reads the
// session's own binding from the shared chat-session query cache entry
// (family "build") instead of the chooser's original selection.
function useSessionBinding(sessionId: string | null): Binding {
  const { data, isError } = useChatSessionQuery("build", sessionId, (id) =>
    api.getBuildChatSession(id),
  );
  return {
    projectId: data?.projectId ?? null,
    // Only a never-successful read is a genuine, distinct unresolved state — a
    // resolved session with no repo of its own still falls through to the
    // default via the undefined branch, and a failed background refresh after
    // a prior success keeps the last known binding.
    engineKey: isError && data === undefined ? null : data?.engineKey,
  };
}

function createFromBinding(
  binding: Binding,
  fallbackKey: string | null,
): Promise<{ session_id: string }> {
  if (binding.engineKey === null) {
    return Promise.reject(new Error("repository unresolved for this session"));
  }
  const engineKey = binding.engineKey ?? fallbackKey;
  if (engineKey === null) {
    return Promise.reject(new Error("default repository not loaded"));
  }
  return api.createBuildChatSession(engineKey, binding.projectId ?? undefined);
}

export function useBuildChatSession(sessionId: string | null): UseBuildChatSessionResult {
  const binding = useSessionBinding(sessionId);
  const { loaded, defaultEngineKey } = useEngines();
  const fallbackKey = loaded ? defaultEngineKey : null;
  const chatApi = useMemo<ChatSessionApi<ChatBrief>>(
    () => ({
      family: "build",
      get: api.getBuildChatSession,
      subscribe: api.subscribeBuildChatSession,
      send: api.sendBuildChatMessage,
      interrupt: api.interruptBuildChatSession,
      summarize: api.summarizeBuildChat,
      putBrief: api.putBuildChatBrief,
      delete: api.deleteBuildChatSession,
      createNew: () => createFromBinding(binding, fallbackKey),
      noSessionError: "no build-chat session",
    }),
    [binding, fallbackKey],
  );
  const base = useChatSessionGeneric<ChatBrief>(sessionId, chatApi);
  return {
    ...base,
    summarize: base.summarize!,
    updateBrief: base.updateBrief!,
    projectId: binding.projectId,
    engineKey: binding.engineKey ?? null,
  };
}
