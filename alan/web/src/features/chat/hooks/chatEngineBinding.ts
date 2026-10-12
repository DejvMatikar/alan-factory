export interface ResolveBuildEngineKeyArgs {
  projectId: string | undefined;
  projectEngineKey: string | null | undefined;
  selectedEngineKey: string | undefined;
  onlyDefaultRepo: boolean;
  defaultKey: string | null;
}

export function resolveBuildEngineKey({
  projectId,
  projectEngineKey,
  selectedEngineKey,
  onlyDefaultRepo,
  defaultKey,
}: Readonly<ResolveBuildEngineKeyArgs>): string | null {
  if (projectId !== undefined) return projectEngineKey ?? null;
  if (selectedEngineKey !== undefined) return selectedEngineKey;
  return onlyDefaultRepo ? defaultKey : null;
}

export function resolveExploreEngineKey(
  selectedEngineKey: string | undefined,
  onlyDefaultRepo: boolean,
  defaultKey: string | null,
): string | null {
  if (selectedEngineKey !== undefined) return selectedEngineKey;
  return onlyDefaultRepo ? defaultKey : null;
}
