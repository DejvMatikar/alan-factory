import type { ReactNode } from "react";

import type { AgentEntry } from "../../../api";
import AgentPromptViewer from "../../../components/AgentPromptViewer";
import LaunchSetupList, { entryLaunchValues } from "../../../components/LaunchSetupList";
import { AGENT_KIND_LABEL } from "../../../lib/agentSearch";
import Disclosure from "../../../ui/Disclosure";
import ErrorBanner from "../../../ui/ErrorBanner";
import AgentFlagList from "./AgentFlagList";

const CHIP =
  "inline-flex h-6 items-center rounded border border-border px-1.5 text-xs text-text-muted";
const WARN_CHIP = "inline-flex h-6 items-center rounded bg-amber-500/15 px-1.5 text-xs text-amber-300";

const SCOPE_LABEL: Readonly<Record<AgentEntry["scope"], string>> = {
  repo: "Depends on this repository",
  machine: "Same for every repository",
  default_engine: "Always the default engine",
};

const TAG_LABEL: Readonly<Record<NonNullable<AgentEntry["tags"]>[number], string>> = {
  eval: "Eval",
  driver_loop: "Driver loop",
  legacy: "Legacy",
  consultant: "Consultant",
  template: "Template",
};

export interface AgentRowProps {
  readonly entry: AgentEntry;
  readonly promptMatches: number;
  readonly term: string;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
}

function Chip({ children, className = CHIP }: Readonly<{ children: ReactNode; className?: string }>) {
  return <span className={className}>{children}</span>;
}

function matchCountText(count: number): string {
  return `${String(count)} ${count === 1 ? "match" : "matches"} in prompt`;
}

function AgentRowLabel({ entry, promptMatches, term }: Readonly<Omit<AgentRowProps, "open" | "onOpenChange">>) {
  const flagCount = (entry.flags ?? []).length;
  return (
    <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
      <span className="break-words font-medium leading-6 text-text-primary">{entry.name}</span>
      <Chip>{AGENT_KIND_LABEL[entry.kind]}</Chip>
      <span className="font-mono text-xs leading-6 text-text-muted">
        {entry.model.display} · effort {entry.effort.display}
      </span>
      {entry.prompt_overridden && <Chip>overridden</Chip>}
      {flagCount > 0 && <Chip className={WARN_CHIP}>{`${String(flagCount)} flagged`}</Chip>}
      {term.trim() !== "" && <Chip>{matchCountText(promptMatches)}</Chip>}
    </span>
  );
}

function AgentIntro({ entry }: Readonly<{ entry: AgentEntry }>) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-text-primary" data-testid={`agent-${entry.id}-when`}>
        {entry.when_runs}
      </p>
      <p className="flex flex-wrap gap-2" data-testid={`agent-${entry.id}-chips`}>
        <Chip>{SCOPE_LABEL[entry.scope]}</Chip>
        {(entry.tags ?? []).map((tag) => (
          <Chip key={tag}>{TAG_LABEL[tag]}</Chip>
        ))}
        <Chip>id {entry.id}</Chip>
      </p>
    </div>
  );
}

function PromptSections({ entry, term }: Readonly<{ entry: AgentEntry; term: string }>) {
  const prompt = entry.prompt ?? [];
  const userMessage = entry.user_message ?? [];
  return (
    <>
      {prompt.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid={`agent-${entry.id}-no-prompt`}>
          No appended system prompt.
        </p>
      ) : (
        <AgentPromptViewer
          segments={prompt}
          term={term}
          title={entry.prompt_overridden ? "Appended system prompt (overridden)" : "Appended system prompt"}
          testId={`agent-${entry.id}-prompt`}
        />
      )}
      {userMessage.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid={`agent-${entry.id}-no-user-message`}>
          No first user message template.
        </p>
      ) : (
        <AgentPromptViewer
          segments={userMessage}
          title="First user message template"
          testId={`agent-${entry.id}-user-message`}
        />
      )}
    </>
  );
}

function AgentRowBody({ entry, term }: Readonly<{ entry: AgentEntry; term: string }>) {
  return (
    <div className="space-y-4 py-3" data-testid={`agent-${entry.id}-body`}>
      <AgentIntro entry={entry} />
      {entry.error != null && (
        <ErrorBanner data-testid={`agent-${entry.id}-error`}>{entry.error}</ErrorBanner>
      )}
      <AgentFlagList flags={entry.flags ?? []} testId={`agent-${entry.id}-flags`} />
      <PromptSections entry={entry} term={term} />
      <LaunchSetupList values={entryLaunchValues(entry)} testId={`agent-${entry.id}-setup`} />
    </div>
  );
}

export default function AgentRow({ entry, promptMatches, term, open, onOpenChange }: Readonly<AgentRowProps>) {
  return (
    <li className="rounded-lg border border-border bg-surface-elevated px-3 py-2">
      <Disclosure
        preset="row"
        defaultOpen={false}
        open={open}
        onOpenChange={onOpenChange}
        testId={`agent-row-${entry.id}`}
        summaryTestId={`agent-row-${entry.id}-summary`}
        label={<AgentRowLabel entry={entry} promptMatches={promptMatches} term={term} />}
      >
        {open && <AgentRowBody entry={entry} term={term} />}
      </Disclosure>
    </li>
  );
}
