import Button from "../../../components/Button";

export interface WizardFooterProps {
  primaryLabel: string;
  onPrimary: () => void;
  primaryDisabled?: boolean;
  caption: string;
  testId: string;
}

// The one saturated-fill primary action per step (rule 7), plus a caption
// directly beneath it stating in plain language exactly what the action does
// (AC7) — shared so every step's footer looks and behaves identically.
export default function WizardFooter({
  primaryLabel,
  onPrimary,
  primaryDisabled,
  caption,
  testId,
}: Readonly<WizardFooterProps>) {
  return (
    <div className="space-y-1" data-testid={testId}>
      <Button
        variant="action"
        data-testid={`${testId}-primary`}
        onClick={onPrimary}
        disabled={primaryDisabled ?? false}
      >
        {primaryLabel}
      </Button>
      <p className="text-xs text-text-muted" data-testid={`${testId}-caption`}>
        {caption}
      </p>
    </div>
  );
}
