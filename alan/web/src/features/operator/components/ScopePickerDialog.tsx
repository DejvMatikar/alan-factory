import { useState } from "react";

import {
  startOperatorSession,
  type OperatorScope,
  type OperatorSession,
  type OperatorStartBody,
} from "../../../api/operator";
import Button from "../../../components/Button";
import DialogFrame from "../../../ui/DialogFrame";
import { useAction } from "../../../ui/useAction";
import { useOperatorTargets, type OperatorTargets } from "../hooks/useOperatorTargets";
import { describeSession, failureText, SCOPE_OPTIONS } from "../operatorCopy";
import { OPERATOR_SCOPES } from "../operatorProtocol";
import TargetSelect from "./TargetSelect";

export interface ScopePreset {
  readonly scope: OperatorScope;
  readonly target: string;
}

export interface ScopePickerDialogProps {
  readonly preset: ScopePreset;
  readonly replacing: OperatorSession | null;
  readonly onStarted: () => void;
  readonly onCancel: () => void;
}

type Picks = Readonly<Partial<Record<OperatorScope, string>>>;

interface Choice {
  readonly scope: OperatorScope;
  readonly targets: OperatorTargets;
  readonly target: string;
  readonly chooseScope: (next: OperatorScope) => void;
  readonly chooseTarget: (next: string) => void;
}

function useChoice(preset: ScopePreset): Choice {
  const [scope, setScope] = useState<OperatorScope>(preset.scope);
  const [picks, setPicks] = useState<Picks>({});
  const targets = useOperatorTargets(scope, preset.scope === scope ? preset.target : "");
  return {
    scope,
    targets,
    target: picks[scope] ?? targets.initial,
    chooseScope: setScope,
    chooseTarget: (next) => {
      setPicks((previous) => ({ ...previous, [scope]: next }));
    },
  };
}

function isValidChoice({ scope, targets, target }: Choice): boolean {
  return (
    scope === "fleet" ||
    (targets.status === "ready" && targets.options.some((option) => option.value === target))
  );
}

function ScopeChoice({
  scope,
  onChange,
}: Readonly<{ scope: OperatorScope; onChange: (next: OperatorScope) => void }>) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-xs font-medium text-text-muted">Scope</legend>
      {OPERATOR_SCOPES.map((option) => (
        <label
          key={option}
          className="flex min-h-11 items-start gap-2 py-1 text-sm text-text-primary"
        >
          <input
            type="radio"
            name="operator-scope"
            value={option}
            data-testid={`operator-scope-${option}`}
            checked={scope === option}
            onChange={() => {
              onChange(option);
            }}
            className="mt-1"
          />
          <span>
            {SCOPE_OPTIONS[option].label}
            <span className="block text-xs text-text-muted">{SCOPE_OPTIONS[option].hint}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function ReplaceConfirm({
  session,
  confirmed,
  onChange,
}: Readonly<{ session: OperatorSession; confirmed: boolean; onChange: (next: boolean) => void }>) {
  return (
    <div className="space-y-2 rounded-md border border-border px-3 py-2 text-sm text-text-primary">
      <p data-testid="operator-replace-notice">
        A session is already running ({describeSession(session)}). Starting a new one ends it
        first.
      </p>
      <label className="flex min-h-11 items-center gap-2">
        <input
          type="checkbox"
          data-testid="operator-replace-confirm"
          checked={confirmed}
          onChange={(e) => {
            onChange(e.target.checked);
          }}
        />
        <span>Replace the running session</span>
      </label>
    </div>
  );
}

function canStart(choice: Choice, replacing: OperatorSession | null, confirmed: boolean): boolean {
  return isValidChoice(choice) && (replacing === null || confirmed);
}

function startBody({ scope, target }: Choice, replace: boolean): OperatorStartBody {
  return { scope, target: scope === "fleet" ? null : target, replace };
}

function DialogActions({
  replacing,
  disabled,
  cancelDisabled,
  onCancel,
  onSubmit,
}: Readonly<{
  replacing: boolean;
  disabled: boolean;
  cancelDisabled: boolean;
  onCancel: () => void;
  onSubmit: () => void;
}>) {
  return (
    <div className="flex justify-end gap-2">
      <Button variant="ghost" type="button" onClick={onCancel} disabled={cancelDisabled}>
        Cancel
      </Button>
      <Button
        variant={replacing ? "reset" : "create"}
        type="button"
        data-testid="operator-start-submit"
        onClick={onSubmit}
        disabled={disabled}
      >
        {replacing ? "Replace session" : "Start session"}
      </Button>
    </div>
  );
}

export default function ScopePickerDialog({
  preset,
  replacing,
  onStarted,
  onCancel,
}: Readonly<ScopePickerDialogProps>) {
  const choice = useChoice(preset);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { busy, run } = useAction();
  const startable = canStart(choice, replacing, confirmed) && !busy;

  const submit = (): void => {
    if (!startable) return;
    setError(null);
    run(() => startOperatorSession(startBody(choice, replacing !== null)))
      .then(onStarted)
      .catch((err: unknown) => {
        setError(failureText(err));
      });
  };

  return (
    <DialogFrame
      size="md"
      ariaLabel="Start an operator session"
      testId="operator-scope-dialog"
      busy={busy}
      onClose={onCancel}
      onSubmit={submit}
    >
      <h2 className="text-base font-semibold text-text-primary">Start an operator session</h2>
      <ScopeChoice scope={choice.scope} onChange={choice.chooseScope} />
      {choice.scope !== "fleet" && (
        <TargetSelect
          scope={choice.scope}
          targets={choice.targets}
          value={choice.target}
          onChange={choice.chooseTarget}
        />
      )}
      {replacing !== null && (
        <ReplaceConfirm session={replacing} confirmed={confirmed} onChange={setConfirmed} />
      )}
      {error !== null && (
        <p role="alert" data-testid="operator-start-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
      <DialogActions
        replacing={replacing !== null}
        disabled={!startable}
        cancelDisabled={busy}
        onCancel={onCancel}
        onSubmit={submit}
      />
    </DialogFrame>
  );
}
