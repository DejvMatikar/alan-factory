import { Link } from "react-router-dom";

import type { CollisionMatchInfo } from "../../../api";
import type { CollisionInfo } from "../hooks/useCollisionCheck";

function MatchLink({ match }: Readonly<{ match: CollisionMatchInfo }>) {
  if (match.kind === "workspace") {
    return (
      <Link to={match.deep_link} className="underline">
        {match.label}
      </Link>
    );
  }
  return (
    <a href={match.deep_link} target="_blank" rel="noreferrer noopener" className="underline">
      {match.label}
    </a>
  );
}

const STATE_CLASS: Record<"green" | "yellow" | "red", string> = {
  green: "border-green-500/30 bg-green-500/10 text-green-300",
  yellow: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  red: "border-red-500/30 bg-red-500/15 text-red-300",
};

export interface CollisionBoxProps {
  info: CollisionInfo;
  onRerun: () => void;
}

export default function CollisionBox({
  info,
  onRerun,
}: Readonly<CollisionBoxProps>) {
  if (info.status === "loading") {
    return (
      <div
        data-testid="collision-box"
        className="rounded-md border border-border bg-surface px-3 py-2 text-sm text-text-muted"
      >
        Checking for overlapping work…
      </div>
    );
  }
  const cls = STATE_CLASS[info.status];
  return (
    <div
      data-testid="collision-box"
      className={`rounded-md border px-3 py-2 text-sm ${cls}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="space-y-1">
          <p data-testid="collision-sentence">{info.sentence}</p>
          {info.match !== null && (
            <p data-testid="collision-match-link">
              <MatchLink match={info.match} />
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onRerun}
          data-testid="collision-rerun"
          className="shrink-0 rounded border border-current px-2 py-0.5 text-xs opacity-70 hover:opacity-100"
        >
          Re-run
        </button>
      </div>
    </div>
  );
}
