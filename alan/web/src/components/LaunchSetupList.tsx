import type { ReactNode } from "react";

import type { AgentEntry, AgentValue, LaunchRecord } from "../api";

export const LAUNCH_FIELD_IDS = [
  "model",
  "effort",
  "turn_limit",
  "permission_mode",
  "tools_stored",
  "tools_effective",
  "tools_denied",
  "add_dirs",
  "session",
  "working_directory",
  "timeouts",
  "environment",
  "process",
] as const;

export type LaunchFieldId = (typeof LAUNCH_FIELD_IDS)[number];

export type LaunchSetupValues = Partial<Record<LaunchFieldId, ReactNode>>;

const LAUNCH_FIELD_LABEL: Readonly<Record<LaunchFieldId, string>> = {
  model: "Model",
  effort: "Effort",
  turn_limit: "Turn limit",
  permission_mode: "Permission mode",
  tools_stored: "Allowed tools (stored)",
  tools_effective: "Allowed tools (effective)",
  tools_denied: "Denied tools",
  add_dirs: "Extra readable directories",
  session: "Session",
  working_directory: "Working directory",
  timeouts: "Timeouts",
  environment: "Environment (names and categories only)",
  process: "Process priority and detachment",
};

const SOURCE_LABEL: Readonly<Record<Exclude<AgentValue["source"], "cli_default">, string>> = {
  engine_role: "Engine role",
  builtin: "Built-in",
};

const SESSION_LABEL: Readonly<Record<string, string>> = {
  new: "New session",
  resumed: "Resumed session",
  resumable: "Resumable session",
  per_message: "One session per message",
  interactive: "Interactive session",
  none: "No session",
};

function sessionLabel(mode: string): string {
  return SESSION_LABEL[mode] ?? mode;
}

const MUTED = "text-text-muted";
const CHIP = "rounded bg-surface px-1.5 py-0.5 font-mono text-xs text-text-primary break-all";

export const NOT_RECORDED_TEXT = "not recorded for this run";

export const RUN_LAUNCH_FIELD_IDS: readonly LaunchFieldId[] = LAUNCH_FIELD_IDS.filter(
  (id) => id !== "tools_stored",
);

function Muted({ children }: Readonly<{ children: ReactNode }>) {
  return <span className={MUTED}>{children}</span>;
}

function NotRecorded() {
  return <span className={`${MUTED} italic`}>{NOT_RECORDED_TEXT}</span>;
}

function ChipList({ items, empty = "none" }: Readonly<{ items: readonly string[]; empty?: string }>) {
  if (items.length === 0) return <Muted>{empty}</Muted>;
  return (
    <ul className="flex flex-wrap gap-1">
      {items.map((item) => (
        <li key={item} className={CHIP}>
          {item}
        </li>
      ))}
    </ul>
  );
}

function Stacked({ children }: Readonly<{ children: ReactNode }>) {
  return <div className="space-y-1">{children}</div>;
}

function ValueWithSource({ value }: Readonly<{ value: AgentValue }>) {
  return (
    <Stacked>
      <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-mono text-text-primary">{value.display}</span>
        {value.source !== "cli_default" && (
          <span className="rounded border border-border px-1.5 py-0.5 text-xs text-text-muted">
            {SOURCE_LABEL[value.source]}
          </span>
        )}
      </span>
      {value.source_detail != null && <Muted>{value.source_detail}</Muted>}
    </Stacked>
  );
}

function secondsText(seconds: number | null | undefined): string {
  return seconds == null ? "no limit" : `${String(seconds)}s`;
}

function timeoutsText(idle: number | null | undefined, absolute: number | null | undefined): string {
  return `idle ${secondsText(idle)} · absolute ${secondsText(absolute)}`;
}

function priorityText(delta: number | null | undefined): string {
  if (delta == null) return "default priority";
  return `priority ${delta > 0 ? "+" : ""}${String(delta)}`;
}

function envList(environment: readonly { name: string; category: string }[]): ReactNode {
  return (
    <ChipList items={environment.map((entry) => `${entry.name} (${entry.category})`)} empty="none set by alan" />
  );
}

function directoryList(entry: AgentEntry): ReactNode {
  const conditional = entry.conditional_add_dirs ?? [];
  return (
    <Stacked>
      <ChipList items={entry.add_dirs ?? []} />
      {conditional.map((dir) => (
        <p key={dir.label} className={`${MUTED} text-xs`}>
          <span className="font-mono text-text-primary">{dir.label}</span> — {dir.condition}
        </p>
      ))}
    </Stacked>
  );
}

function turnLimitValue(entry: AgentEntry): ReactNode {
  const { max_turns: maxTurns, adaptive, note } = entry.turn_limit;
  return (
    <Stacked>
      <span>
        {maxTurns === null ? "no cap set by alan" : String(maxTurns)}
        {adaptive && <Muted> · adaptive budget</Muted>}
      </span>
      {note != null && <Muted>{note}</Muted>}
    </Stacked>
  );
}

function detailed(text: string, detail: string | null | undefined): ReactNode {
  return (
    <Stacked>
      <span>{text}</span>
      {detail != null && <Muted>{detail}</Muted>}
    </Stacked>
  );
}

function processValue(entry: AgentEntry): ReactNode {
  const { priority_delta: delta, detached, detail } = entry.process;
  return detailed(`${priorityText(delta)} · ${detached ? "detached" : "attached"}`, detail);
}

function environmentValue(entry: AgentEntry): ReactNode {
  return (
    <Stacked>
      {envList(entry.environment ?? [])}
      {entry.environment_note != null && <Muted>{entry.environment_note}</Muted>}
    </Stacked>
  );
}

export function entryLaunchValues(entry: AgentEntry): LaunchSetupValues {
  return {
    model: <ValueWithSource value={entry.model} />,
    effort: <ValueWithSource value={entry.effort} />,
    turn_limit: turnLimitValue(entry),
    permission_mode: entry.permission_mode ?? <Muted>CLI default</Muted>,
    tools_stored: <ChipList items={entry.tools.stored ?? []} />,
    tools_effective: <ChipList items={entry.tools.effective ?? []} />,
    tools_denied: <ChipList items={entry.tools.denied ?? []} />,
    add_dirs: directoryList(entry),
    session: detailed(sessionLabel(entry.session.behaviour), entry.session.detail),
    working_directory:
      entry.working_directory === "" ? <Muted>not resolved for this agent</Muted> : entry.working_directory,
    timeouts: detailed(timeoutsText(entry.timeouts.idle_seconds, entry.timeouts.absolute_seconds), entry.timeouts.detail),
    environment: environmentValue(entry),
    process: processValue(entry),
  };
}

function recordTools(record: LaunchRecord): ReactNode {
  return record.deny_all_tools ? <Muted>all tools denied</Muted> : <ChipList items={record.allowed_tools} />;
}

function recordProcess(record: LaunchRecord): string {
  const parts = [priorityText(record.priority_delta), record.detached ? "detached" : "attached"];
  return record.fleet ? [...parts, "fleet worker"].join(" · ") : parts.join(" · ");
}

function recordedValue(value: string | null, fallback: string): ReactNode {
  return value === null ? <Muted>{fallback}</Muted> : <span className="font-mono">{value}</span>;
}

export function recordLaunchValues(record: LaunchRecord): LaunchSetupValues {
  return {
    model: recordedValue(record.model, "CLI default"),
    effort: recordedValue(record.effort, "CLI default"),
    turn_limit: record.max_turns === null ? "no cap set by alan" : String(record.max_turns),
    permission_mode: recordedValue(record.permission_mode, "CLI default"),
    tools_effective: recordTools(record),
    tools_denied: <ChipList items={record.denied_tools} />,
    add_dirs: <ChipList items={record.add_dirs} />,
    session: sessionLabel(record.session_mode),
    working_directory: record.cwd ?? <Muted>inherited from the alan service process</Muted>,
    timeouts: timeoutsText(record.idle_timeout_seconds, record.abs_timeout_seconds),
    environment: envList(record.env),
    process: recordProcess(record),
  };
}

export function notRecordedLaunchValues(): LaunchSetupValues {
  return Object.fromEntries(RUN_LAUNCH_FIELD_IDS.map((id) => [id, <NotRecorded key={id} />]));
}

export interface LaunchSetupListProps {
  readonly values: LaunchSetupValues;
  readonly testId: string;
}

export default function LaunchSetupList({ values, testId }: Readonly<LaunchSetupListProps>) {
  return (
    <dl className="space-y-3 text-sm" data-testid={testId}>
      {LAUNCH_FIELD_IDS.filter((id) => values[id] !== undefined).map((id) => (
        <div key={id} className="flex flex-col gap-1 sm:flex-row sm:gap-4" data-testid={`${testId}-${id}`}>
          <dt className="shrink-0 text-xs font-medium text-text-muted sm:w-56">{LAUNCH_FIELD_LABEL[id]}</dt>
          <dd className="min-w-0 break-words text-text-primary">{values[id]}</dd>
        </div>
      ))}
    </dl>
  );
}
