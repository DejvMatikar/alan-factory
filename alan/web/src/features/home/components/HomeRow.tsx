import type { ReactNode } from "react";

import SubduedActionControl from "../../../components/SubduedActionControl";

export type HomeRowKind = "project" | "unassigned" | "feature";

export interface HomeRowAction {
  readonly testId: string;
  readonly label: string;
  readonly description: string;
  readonly disabled: boolean;
  readonly busy: boolean;
  readonly error: string | null;
  readonly onClick: () => void;
  readonly icon: ReactNode;
  readonly ariaLabel: string;
}

export interface HomeRowCells {
  readonly primary: ReactNode;
  readonly team: ReactNode;
  readonly state: ReactNode;
  readonly phase: ReactNode;
  readonly done: ReactNode;
  readonly active: ReactNode;
  readonly cost: ReactNode;
  readonly updated: ReactNode;
}

interface HomeRowProps {
  readonly kind: HomeRowKind;
  readonly testId: string;
  readonly cells: HomeRowCells;
  readonly action?: HomeRowAction;
}

const ROW_CLASS: Record<HomeRowKind, string> = {
  project: "border-b border-border/60 bg-surface-elevated/40",
  unassigned: "border-b border-border/60 bg-surface-elevated/20 text-text-muted",
  feature: "border-b border-border/60 hover:bg-surface-elevated [&>td]:align-middle",
};

const PRIMARY_CLASS: Record<HomeRowKind, string> = {
  project: "py-2 pr-4 pl-2 font-medium text-text-primary",
  unassigned: "py-2 pr-4 pl-2 font-medium text-text-muted",
  feature: "w-full max-w-0 py-2 pr-4 pl-2",
};

function ActionCell({ action }: Readonly<{ action: HomeRowAction | undefined }>) {
  if (!action) return <td className="w-11" />;
  return (
    <td className="w-11 py-2">
      <SubduedActionControl
        testId={action.testId}
        label={action.label}
        description={action.description}
        disabled={action.disabled}
        ariaBusy={action.busy}
        ariaLabel={action.ariaLabel}
        onClick={action.onClick}
        icon={action.icon}
        variant="action"
      />
      {action.error !== null && (
        <p role="alert" className="mt-1 text-xs text-btn-destroy break-words">
          {action.error}
        </p>
      )}
    </td>
  );
}

export default function HomeRow({ kind, testId, cells, action }: Readonly<HomeRowProps>) {
  return (
    <tr data-testid={testId} className={ROW_CLASS[kind]}>
      <ActionCell action={action} />
      <td className={PRIMARY_CLASS[kind]}>{cells.primary}</td>
      <td className="py-2 pr-4 text-text-muted">{cells.team}</td>
      <td className="py-2 pr-4">{cells.state}</td>
      <td className="py-2 pr-4 text-xs text-text-muted">{cells.phase}</td>
      <td className="py-2 pr-4 text-xs tabular-nums text-text-muted">{cells.done}</td>
      <td className="py-2 pr-4 text-xs tabular-nums text-text-muted">{cells.active}</td>
      <td className="py-2 pr-4 text-xs tabular-nums text-text-muted">{cells.cost}</td>
      <td className="py-2 pr-4 text-xs tabular-nums text-text-muted">{cells.updated}</td>
    </tr>
  );
}
