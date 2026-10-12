export type WizardStepStatus = "done" | "current" | "upcoming";

export interface WizardRailStep {
  key: string;
  label: string;
}

export interface WizardRailProps {
  steps: readonly WizardRailStep[];
  currentKey: string;
}

const STATUS_TEXT: Record<WizardStepStatus, string> = {
  done: "done",
  current: "current",
  upcoming: "upcoming",
};

const STATUS_CLASSES: Record<WizardStepStatus, string> = {
  done: "bg-btn-create/15 text-btn-create",
  current: "bg-btn-action/15 text-btn-action",
  upcoming: "bg-surface text-text-muted",
};

function statusFor(index: number, currentIndex: number): WizardStepStatus {
  if (index === currentIndex) return "current";
  if (currentIndex !== -1 && index < currentIndex) return "done";
  return "upcoming";
}

// The four-step progress rail (identify -> configure -> make dispatchable ->
// posture). Generic over "a list of steps and which one is current" — steps
// 2-4 render as ordinary upcoming entries, not a special "not built yet"
// state, so later PRs need no rail change to plug their own step in.
export default function WizardRail({ steps, currentKey }: Readonly<WizardRailProps>) {
  const currentIndex = steps.findIndex((step) => step.key === currentKey);
  return (
    <ol
      className="flex flex-wrap gap-2"
      data-testid="wizard-rail"
      aria-label="Connect repository steps"
    >
      {steps.map((step, index) => {
        const status = statusFor(index, currentIndex);
        return (
          <li
            key={step.key}
            data-testid={`wizard-rail-step-${step.key}`}
            aria-current={status === "current" ? "step" : undefined}
            className={`rounded px-2 py-0.5 text-xs ${STATUS_CLASSES[status]}`}
          >
            {step.label}
            <span className="sr-only"> ({STATUS_TEXT[status]})</span>
          </li>
        );
      })}
    </ol>
  );
}
