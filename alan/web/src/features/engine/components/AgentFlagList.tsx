import type { AgentFlag } from "../../../api";

const SEVERITY_STYLE: Readonly<Record<AgentFlag["severity"], string>> = {
  warn: "bg-amber-500/15 text-amber-300",
  info: "bg-slate-500/15 text-slate-300",
};

const SEVERITY_LABEL: Readonly<Record<AgentFlag["severity"], string>> = {
  warn: "Warning",
  info: "Note",
};

export interface AgentFlagListProps {
  readonly flags: readonly AgentFlag[];
  readonly testId: string;
}

export default function AgentFlagList({ flags, testId }: Readonly<AgentFlagListProps>) {
  if (flags.length === 0) return null;
  return (
    <ul className="space-y-2" data-testid={testId} aria-label="Flagged configuration problems">
      {flags.map((flag) => (
        <li
          key={`${flag.code}:${flag.title}`}
          className={`space-y-1 rounded-md px-3 py-2 text-xs ${SEVERITY_STYLE[flag.severity]}`}
          data-testid={`${testId}-${flag.code}`}
        >
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="font-semibold uppercase tracking-wide">{SEVERITY_LABEL[flag.severity]}</span>
            <span className="font-medium">{flag.title}</span>
            <code className="text-[10px] opacity-80">{flag.code}</code>
          </p>
          <p className="break-words">{flag.detail}</p>
        </li>
      ))}
    </ul>
  );
}
