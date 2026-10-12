import { api, ApiError, type EngineConfig, type EngineSource } from "../../../api";
import { useDocQuery, useResetDoc } from "../../../api/queries";
import { errorMessageOf } from "../../../lib/errorMessage";

export type UseEngineConfigDocResult =
  | { status: "loading" }
  | { status: "error"; message: string; refetch: () => void }
  | { status: "empty"; refetch: () => void }
  | { status: "ready"; config: EngineConfig; source: EngineSource | null; refetch: () => void };

interface EngineConfigDoc {
  config: EngineConfig;
  source: EngineSource | null;
}

type EngineConfigFetchOutcome = { kind: "missing" } | { kind: "found"; doc: EngineConfigDoc };

type ConfigFetchOutcome = { kind: "found"; config: EngineConfig } | { kind: "missing" };

// A 404 from the config fetch means this repository has never been
// registered as an engine — a distinct "empty" outcome, not an error.
function fetchConfig(engineKey: string): Promise<ConfigFetchOutcome> {
  return api.getEngineConfig(engineKey).then(
    (config): ConfigFetchOutcome => ({ kind: "found", config }),
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 404) return { kind: "missing" };
      throw err;
    },
  );
}

// The sources list is a courtesy on top of the backend's own structural-PATCH
// enforcement, not the enforcement itself — a failed sources fetch degrades to
// source: null (unknown) rather than failing the whole doc load, so a single
// secondary endpoint outage never blocks the config screen.
async function fetchEngineConfigDoc(engineKey: string): Promise<EngineConfigFetchOutcome> {
  const [configResult, sources] = await Promise.all([
    fetchConfig(engineKey),
    api.getEngineSources().catch(() => null),
  ]);
  if (configResult.kind === "missing") return { kind: "missing" };
  const source = sources?.find((s) => s.slug === engineKey) ?? null;
  return { kind: "found", doc: { config: configResult.config, source } };
}

export function useEngineConfigDoc(engineKey: string | undefined): UseEngineConfigDocResult {
  const view = useDocQuery("engine-config", engineKey, fetchEngineConfigDoc);
  const refetch = useResetDoc("engine-config", engineKey);
  if (view.isError) return { status: "error", message: errorMessageOf(view.error), refetch };
  if (view.data === undefined) return { status: "loading" };
  if (view.data.kind === "missing") return { status: "empty", refetch };
  return {
    status: "ready",
    config: view.data.doc.config,
    source: view.data.doc.source,
    refetch,
  };
}
