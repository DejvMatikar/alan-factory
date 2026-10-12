import { useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import type { AgentCatalogue } from "../../../api";
import Button from "../../../components/Button";
import {
  agentFilterOptions,
  parseAgentKind,
  searchAgents,
  type AgentFilters,
} from "../../../lib/agentSearch";
import ErrorBanner from "../../../ui/ErrorBanner";
import SectionHeading from "../../../ui/SectionHeading";
import AgentFlagList from "../components/AgentFlagList";
import AgentRow from "../components/AgentRow";
import AgentSearchBar from "../components/AgentSearchBar";
import { useAgentCatalogue } from "../hooks/useAgentCatalogue";

const PARAM_QUERY = "q";
const PARAM_AGENT = "agent";
const PARAM_MODEL = "model";
const PARAM_EFFORT = "effort";
const PARAM_KIND = "kind";

const FILTER_PARAM: Readonly<Record<keyof AgentFilters, string>> = {
  model: PARAM_MODEL,
  effort: PARAM_EFFORT,
  kind: PARAM_KIND,
};

const CLEARED_PARAMS: Readonly<Record<string, null>> = {
  [PARAM_QUERY]: null,
  [PARAM_AGENT]: null,
  [PARAM_MODEL]: null,
  [PARAM_EFFORT]: null,
  [PARAM_KIND]: null,
};

function nonEmpty(value: string | null): string | null {
  return value === "" ? null : value;
}

function readFilters(params: URLSearchParams): AgentFilters {
  return {
    model: nonEmpty(params.get(PARAM_MODEL)),
    effort: nonEmpty(params.get(PARAM_EFFORT)),
    kind: parseAgentKind(params.get(PARAM_KIND)),
  };
}

function withParams(
  base: URLSearchParams,
  changes: Readonly<Record<string, string | null>>,
): URLSearchParams {
  const next = new URLSearchParams(base);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === "") next.delete(key);
    else next.set(key, value);
  }
  return next;
}

function CatalogueNotes({ notes }: Readonly<{ notes: AgentCatalogue["notes"] }>) {
  return (
    <section
      aria-label="How to read this catalogue"
      className="space-y-3 rounded-lg border border-border bg-surface-elevated px-4 py-3"
      data-testid="repo-agents-notes"
    >
      <p className="text-sm text-text-primary" data-testid="repo-agents-appended-note">
        {notes.appended_prompt}
      </p>
      <div className="space-y-1">
        <SectionHeading level="h4">Inherited from Claude Code on this machine</SectionHeading>
        <p className="text-xs text-text-muted">
          Every agent also inherits the items below. They are named only — their contents are not
          read or shown here.
        </p>
        <ul className="space-y-1 text-sm text-text-primary" data-testid="repo-agents-inherited">
          {notes.inherited.map((item) => (
            <li key={item.name}>
              <span className="font-medium">{item.name}</span>
              <span className="text-text-muted"> — {item.note}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function CatalogueFlags({ flags }: Readonly<{ flags: AgentCatalogue["flags"] }>) {
  if (flags === undefined || flags.length === 0) return null;
  return (
    <section className="space-y-2" data-testid="repo-agents-flags-section">
      <SectionHeading level="h4">Flagged for the whole catalogue</SectionHeading>
      <AgentFlagList flags={flags} testId="repo-agents-flags" />
    </section>
  );
}

function NoMatches({ onClear }: Readonly<{ onClear: () => void }>) {
  return (
    <div
      className="space-y-3 rounded-lg border border-dashed border-border bg-surface-elevated/30 p-6"
      data-testid="repo-agents-no-match"
    >
      <p className="text-sm text-text-muted">No agents match the current search and filters.</p>
      <Button variant="ghost" onClick={onClear} data-testid="repo-agents-clear">
        Clear search and filters
      </Button>
    </div>
  );
}

function useSyncedQuery(urlQuery: string) {
  const [query, setQuery] = useState(urlQuery);
  const [syncedFrom, setSyncedFrom] = useState(urlQuery);
  if (urlQuery !== syncedFrom) {
    setSyncedFrom(urlQuery);
    setQuery(urlQuery);
  }
  return [query, setQuery] as const;
}

function CatalogueView({ catalogue }: Readonly<{ catalogue: AgentCatalogue }>) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useSyncedQuery(searchParams.get(PARAM_QUERY) ?? "");
  const filters = useMemo(() => readFilters(searchParams), [searchParams]);
  const expanded = searchParams.get(PARAM_AGENT);
  const options = useMemo(() => agentFilterOptions(catalogue.entries), [catalogue.entries]);
  const hits = useMemo(
    () => searchAgents(catalogue.entries, query, filters),
    [catalogue.entries, query, filters],
  );

  const update = (changes: Readonly<Record<string, string | null>>): void => {
    setSearchParams(withParams(searchParams, changes), { replace: true });
  };
  const clear = (): void => {
    setQuery("");
    update(CLEARED_PARAMS);
  };

  return (
    <div className="space-y-4">
      <AgentSearchBar
        query={query}
        filters={filters}
        options={options}
        shown={hits.length}
        total={catalogue.entries.length}
        onQueryChange={(text) => {
          setQuery(text);
          update({ [PARAM_QUERY]: text });
        }}
        onFilterChange={(key, value) => {
          update({ [FILTER_PARAM[key]]: value });
        }}
      />
      {hits.length === 0 ? (
        <NoMatches onClear={clear} />
      ) : (
        <ul className="space-y-2" data-testid="repo-agents-list">
          {hits.map(({ entry, promptMatches }) => (
            <AgentRow
              key={entry.id}
              entry={entry}
              promptMatches={promptMatches}
              term={query}
              open={expanded === entry.id}
              onOpenChange={(open) => {
                update({ [PARAM_AGENT]: open ? entry.id : null });
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function CatalogueBody({ engineKey }: Readonly<{ engineKey: string }>) {
  const state = useAgentCatalogue(engineKey);
  if (state.status === "loading") {
    return (
      <p className="text-sm text-text-muted" data-testid="repo-agents-loading">
        Loading the agent catalogue…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <ErrorBanner
        data-testid="repo-agents-error"
        action={
          <Button variant="ghost" onClick={state.refetch} data-testid="repo-agents-retry">
            Retry
          </Button>
        }
      >
        {state.message}
      </ErrorBanner>
    );
  }
  return (
    <>
      <CatalogueNotes notes={state.data.notes} />
      <CatalogueFlags flags={state.data.flags} />
      <CatalogueView catalogue={state.data} />
    </>
  );
}

export default function RepoAgents() {
  const { owner, repo } = useParams<{ owner: string; repo: string }>();
  if (owner === undefined || repo === undefined) {
    return (
      <p className="text-sm text-text-muted" data-testid="repo-agents-loading">
        Loading…
      </p>
    );
  }
  const engineKey = `${owner}/${repo}`;
  return (
    <div className="space-y-6" data-testid="repo-agents">
      <div className="space-y-2">
        <h1 className="text-xl font-semibold text-text-primary">Agent catalogue</h1>
        <p className="text-sm text-text-primary" data-testid="repo-agents-identity">
          {engineKey}
        </p>
        <p className="text-sm text-text-muted">
          Every agent alan starts, resolved for this repository: the model, effort, full prompt and
          launch settings it actually runs with. Read-only.
        </p>
        <Button variant="ghost" linkTo={`/repos/${engineKey}/config`} data-testid="repo-agents-back">
          Back to engine configuration
        </Button>
      </div>
      <CatalogueBody engineKey={engineKey} />
    </div>
  );
}
