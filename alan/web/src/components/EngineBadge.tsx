import { useDefaultEngineState } from "../hooks/useEngines";

export default function EngineBadge({
  engineKey,
  testIdSuffix,
  showDefault = false,
}: Readonly<{ engineKey: string | null; testIdSuffix: string; showDefault?: boolean }>) {
  const { key: defaultEngineKey, settled } = useDefaultEngineState();
  if (engineKey === null) return null;
  if (!showDefault && (!settled || engineKey === defaultEngineKey)) return null;
  return (
    <span
      data-testid={`engine-badge-${testIdSuffix}`}
      className="inline-block rounded px-2 py-0.5 text-xs bg-purple-500/15 text-purple-300"
    >
      {engineKey}
    </span>
  );
}
