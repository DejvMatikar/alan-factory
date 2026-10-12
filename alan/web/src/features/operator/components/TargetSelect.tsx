import ErrorBanner from "../../../ui/ErrorBanner";
import Input from "../../../ui/Input";
import type { OperatorTargets } from "../hooks/useOperatorTargets";
import { TARGET_COPY, type TargetScope } from "../operatorCopy";

export interface TargetSelectProps {
  readonly scope: TargetScope;
  readonly targets: OperatorTargets;
  readonly value: string;
  readonly onChange: (next: string) => void;
}

const TARGET_ID = "operator-target";
const STATUS_ID = "operator-target-status";

function TargetNotice({
  scope,
  status,
}: Readonly<{ scope: TargetScope; status: OperatorTargets["status"] }>) {
  if (status === "failed") {
    return (
      <div id={STATUS_ID}>
        <ErrorBanner data-testid="operator-target-error">{TARGET_COPY[scope].failed}</ErrorBanner>
      </div>
    );
  }
  if (status === "empty") {
    return (
      <p id={STATUS_ID} data-testid="operator-target-empty" className="text-sm text-text-muted">
        {TARGET_COPY[scope].empty}
      </p>
    );
  }
  return null;
}

export default function TargetSelect({
  scope,
  targets,
  value,
  onChange,
}: Readonly<TargetSelectProps>) {
  const { status, options } = targets;
  const copy = TARGET_COPY[scope];
  const describedByStatus = status === "failed" || status === "empty";
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-text-muted" htmlFor={TARGET_ID}>
        {copy.label}
      </label>
      <Input
        as="select"
        id={TARGET_ID}
        data-testid={TARGET_ID}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
        }}
        disabled={status !== "ready"}
        aria-busy={status === "loading"}
        aria-describedby={describedByStatus ? STATUS_ID : undefined}
        tapTarget
        focusRing
      >
        <option value="" disabled>
          {status === "loading" ? copy.loading : copy.choose}
        </option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Input>
      <TargetNotice scope={scope} status={status} />
    </div>
  );
}
