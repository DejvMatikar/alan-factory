import { Navigate } from "react-router-dom";

import { useEngines } from "../../../hooks/useEngines";
import { useRepoScope } from "../../../hooks/useRepoScope";
import ConnectBlankSlate from "../components/ConnectBlankSlate";
import { switcherEntries, type RepoRegistryEntry } from "../../../lib/fold";

function resolveRedirectTarget(
  scope: string | null,
  entries: readonly RepoRegistryEntry[],
  defaultKey: string | null,
): string | null {
  if (scope !== null) return scope;
  return entries[0]?.key ?? defaultKey;
}

export default function Home() {
  const { engines, loaded, loadFailed, defaultEngineKey } = useEngines();
  const { key: scope } = useRepoScope(engines, defaultEngineKey);

  if (!loaded) return null;
  const entries = switcherEntries(engines, defaultEngineKey);
  if (!loadFailed && entries.length === 0) return <ConnectBlankSlate />;

  const target = resolveRedirectTarget(scope, entries, defaultEngineKey);
  if (target === null) return null;
  return <Navigate replace to={`/repos/${target}`} />;
}
