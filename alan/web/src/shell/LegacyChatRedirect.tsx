import { useEffect } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { registeredRepoEntries, type RepoRegistryEntry } from "../lib/fold";
import { useEngines } from "../hooks/useEngines";
import { REPO_SCOPE_STORAGE_KEY } from "../hooks/useRepoScope";

export type LegacyChatKind = "build" | "explore";

export function resolveLegacyTargetEngineKey(
  engineParam: string | null,
  mirroredScope: string | null,
  entries: readonly RepoRegistryEntry[],
): string | null {
  const isKnown = (key: string | null): key is string =>
    key !== null && entries.some((e) => e.key === key);
  if (isKnown(engineParam)) return engineParam;
  if (isKnown(mirroredScope)) return mirroredScope;
  return entries[0]?.key ?? null;
}

export function scopedChatSuffix(kind: LegacyChatKind, consultant: string | undefined): string {
  return kind === "build" ? "build" : `explore/${encodeURIComponent(consultant ?? "")}`;
}

function querySuffix(qs: string): string {
  return qs.length > 0 ? `?${qs}` : "";
}

export function buildScopedChatPath(
  engineKey: string,
  kind: LegacyChatKind,
  consultant: string | undefined,
  searchParams: URLSearchParams,
): string {
  const rest = new URLSearchParams(searchParams);
  rest.delete("engine");
  const qs = rest.toString();
  return `/repos/${engineKey}/${scopedChatSuffix(kind, consultant)}${querySuffix(qs)}`;
}

export function useChatEngineSelfCorrect(
  kind: LegacyChatKind,
  chatEngineKey: string | null | undefined,
): void {
  const { owner, repo, consultant } = useParams<{
    owner?: string;
    repo?: string;
    consultant?: string;
  }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  useEffect(() => {
    if (chatEngineKey == null || owner === undefined || repo === undefined) return;
    const scopedKey = `${owner}/${repo}`;
    if (chatEngineKey === scopedKey) return;
    const target = buildScopedChatPath(chatEngineKey, kind, consultant, searchParams);
    Promise.resolve(navigate(target, { replace: true })).catch(
      /* v8 ignore next */
      () => undefined,
    );
  }, [chatEngineKey, owner, repo, consultant, kind, searchParams, navigate]);
}

export interface LegacyChatRedirectProps {
  readonly kind: LegacyChatKind;
}

export default function LegacyChatRedirect({ kind }: Readonly<LegacyChatRedirectProps>) {
  const { consultant } = useParams<{ consultant?: string }>();
  const [searchParams] = useSearchParams();
  const { engines, loaded, defaultEngineKey } = useEngines();
  if (!loaded) return null;
  const entries = registeredRepoEntries(engines, defaultEngineKey);
  const engineKey = resolveLegacyTargetEngineKey(
    searchParams.get("engine"),
    sessionStorage.getItem(REPO_SCOPE_STORAGE_KEY),
    entries,
  );
  if (engineKey === null) return <Navigate replace to="/" />;
  const target = buildScopedChatPath(engineKey, kind, consultant, searchParams);
  return <Navigate replace to={target} />;
}
