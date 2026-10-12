import type { ReactNode } from "react";

export interface WizardStepShellProps {
  title: string;
  rail: ReactNode;
  children: ReactNode;
}

// Shared outer layout every connect-wizard step composes: a page-title
// heading (matches every other Shell screen's text-xl per DESIGN_SYSTEM.md),
// the step rail, and the step's own content. Carries no step-specific
// knowledge so W2.2/W2.3/W2.4 reuse it unchanged.
export default function WizardStepShell({ title, rail, children }: Readonly<WizardStepShellProps>) {
  return (
    <div className="space-y-6" data-testid="wizard-step-shell">
      <h1 className="text-xl font-semibold text-text-primary">{title}</h1>
      {rail}
      <div className="space-y-4">{children}</div>
    </div>
  );
}
