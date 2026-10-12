import type { ReactNode } from "react";

import Button, { type ButtonVariant } from "./Button";
import HoverReveal from "./HoverReveal";

export const SUBDUED_WEIGHT_CLASS =
  "opacity-70 hover:opacity-100 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:hover:opacity-50";

const ICON_BUSY_CLASS = "aria-disabled:opacity-50 aria-disabled:cursor-not-allowed";

export interface SubduedActionControlBaseProps {
  readonly testId: string;
  readonly label: string;
  readonly description: string;
  readonly disabled?: boolean | undefined;
  readonly ariaBusy?: boolean | undefined;
  readonly ariaLabel?: string | undefined;
  readonly onClick?: (() => void) | undefined;
}

export type SubduedActionControlProps =
  | (SubduedActionControlBaseProps & { readonly icon?: undefined; readonly variant?: undefined })
  | (SubduedActionControlBaseProps & { readonly icon: ReactNode; readonly variant: ButtonVariant });

interface RenderProps {
  readonly testId: string;
  readonly label: string;
  readonly disabled: boolean | undefined;
  readonly ariaBusy: boolean | undefined;
  readonly ariaLabel: string | undefined;
  readonly onClick: (() => void) | undefined;
}

function renderIconButton({
  testId,
  label,
  disabled,
  ariaBusy,
  ariaLabel,
  onClick,
  icon,
  variant,
}: RenderProps & { readonly icon: ReactNode; readonly variant: ButtonVariant }) {
  return (
    <Button
      variant={variant}
      iconOnly
      aria-label={ariaLabel ?? label}
      className={ICON_BUSY_CLASS}
      data-testid={testId}
      aria-disabled={disabled}
      aria-busy={ariaBusy}
      onClick={disabled ? undefined : onClick}
    >
      {icon}
    </Button>
  );
}

function renderLabelButton({ testId, label, disabled, ariaBusy, ariaLabel, onClick }: RenderProps) {
  return (
    <Button
      variant="ghost"
      className={SUBDUED_WEIGHT_CLASS}
      data-testid={testId}
      aria-disabled={disabled}
      aria-busy={ariaBusy}
      aria-label={ariaLabel}
      onClick={disabled ? undefined : onClick}
    >
      {label}
    </Button>
  );
}

export default function SubduedActionControl({
  testId,
  label,
  description,
  disabled,
  ariaBusy,
  ariaLabel,
  onClick,
  icon,
  variant,
}: Readonly<SubduedActionControlProps>) {
  const descriptionId = `${testId}-description`;
  const shared: RenderProps = { testId, label, disabled, ariaBusy, ariaLabel, onClick };
  const control =
    icon !== undefined ? renderIconButton({ ...shared, icon, variant }) : renderLabelButton(shared);
  return (
    <HoverReveal id={descriptionId} description={description}>
      {control}
    </HoverReveal>
  );
}
