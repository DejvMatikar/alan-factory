// V1 supersedes the wireframe's lead paragraph, which as drawn reads "learns
// its gates from CI" — the pipeline reads the repository, it never parses
// CI. No copy on this surface may claim CI provenance in any form.
const LEAD_PARAGRAPH =
  "alan works against repositories. It clones one, reads its gates directly from the " +
  "repo, opens PRs against it, and never merges beyond the posture you set. Until a " +
  "repo is connected there is nothing for it to do.";

const WHAT_CONNECTING_DOES = [
  "Reads the repo and proposes an engine config — stacks, gate commands, team roster.",
  "Opens one scaffold PR that you review and merge.",
  "Applies branch protection and names the authoritative checks.",
  "You choose a posture — observe first, autonomous much later.",
] as const;

export default function ConnectBlankSlate() {
  return (
    <div
      data-testid="home-blank-slate"
      className="space-y-4 rounded-lg border border-dashed border-border bg-surface-elevated/30 p-6"
    >
      <h2 className="text-base font-semibold text-text-primary">No repositories connected</h2>
      <p className="text-sm text-text-muted">{LEAD_PARAGRAPH}</p>
      <div className="space-y-1">
        <h3 className="text-xs font-medium uppercase text-text-muted">What connecting does</h3>
        <ol className="list-decimal space-y-1 pl-5 text-sm text-text-primary">
          {WHAT_CONNECTING_DOES.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ol>
      </div>
      <p className="text-xs text-text-muted">
        Connect repo, in the top bar, starts this — one PR to review · nothing dispatches until
        you say so.
      </p>
    </div>
  );
}
