import { Link } from "react-router-dom";

import { useUnblockerActivity } from "../../../hooks/useUnblockerActivity";
import AggregateChip from "../../../components/AggregateChip";
import {
  unblockerCountEntries,
  unblockerCounts,
  unblockerHealthHref,
  type UnblockerCountEntry,
} from "../../../lib/unblockerSummaryFields";

interface UnblockerGroupProps {
  readonly entry: UnblockerCountEntry;
  readonly engineKey: string;
}

function UnblockerGroup({ entry, engineKey }: Readonly<UnblockerGroupProps>) {
  const suffix = `${engineKey}-unblocker-${entry.key}`;
  return (
    <Link
      to={unblockerHealthHref(engineKey)}
      className="flex min-h-11 shrink-0 items-center gap-2 hover:underline"
      data-testid={`repo-status-group-${suffix}`}
    >
      <span className="text-[10px] font-medium uppercase tracking-wide text-text-muted">
        {entry.label}
      </span>
      <AggregateChip state={entry.tone} label={String(entry.value)} testIdSuffix={suffix} />
    </Link>
  );
}

export default function UnblockerSummaryRow({ engineKey }: Readonly<{ engineKey: string }>) {
  const { snapshot, error } = useUnblockerActivity(engineKey);
  if (snapshot === null || error) return null;
  const entries = unblockerCountEntries(unblockerCounts(snapshot));
  if (entries.length === 0) return null;
  return (
    <div
      className="flex flex-wrap items-start gap-4"
      data-testid={`repo-status-unblocker-${engineKey}`}
    >
      {entries.map((entry) => (
        <UnblockerGroup key={entry.key} entry={entry} engineKey={engineKey} />
      ))}
    </div>
  );
}
