import type { ReactNode } from "react";

import Button from "../../../components/Button";
import HoverReveal from "../../../components/HoverReveal";
import SubduedActionControl, { SUBDUED_WEIGHT_CLASS } from "../../../components/SubduedActionControl";

export interface RepoActionControlProps {
  readonly repoKey: string;
  readonly label: string;
  readonly description: string;
  readonly disabled?: boolean;
  readonly ariaBusy?: boolean;
  readonly onClick?: () => void;
  readonly linkTo?: string;
  readonly testId: string;
  readonly ariaLabel?: string;
  readonly icon?: ReactNode;
}

function renderIconLink(
  fullTestId: string,
  linkTo: string,
  ariaLabel: string,
  icon: ReactNode,
): ReactNode {
  return (
    <Button variant="ghost" iconOnly aria-label={ariaLabel} linkTo={linkTo} data-testid={fullTestId}>
      {icon}
    </Button>
  );
}

function renderLabelLink(
  fullTestId: string,
  linkTo: string,
  ariaLabel: string | undefined,
  label: string,
): ReactNode {
  return (
    <Button
      variant="ghost"
      className={SUBDUED_WEIGHT_CLASS}
      linkTo={linkTo}
      data-testid={fullTestId}
      aria-label={ariaLabel}
    >
      {label}
    </Button>
  );
}

export default function RepoActionControl({
  repoKey,
  label,
  description,
  disabled,
  ariaBusy,
  onClick,
  linkTo,
  testId,
  ariaLabel,
  icon,
}: Readonly<RepoActionControlProps>) {
  const fullTestId = `${testId}-${repoKey}`;
  const resolvedAriaLabel = ariaLabel ?? label;

  if (linkTo === undefined) {
    return (
      <SubduedActionControl
        testId={fullTestId}
        label={label}
        description={description}
        disabled={disabled}
        ariaBusy={ariaBusy}
        ariaLabel={ariaLabel}
        onClick={onClick}
      />
    );
  }

  const descriptionId = `${fullTestId}-description`;
  const control =
    icon !== undefined
      ? renderIconLink(fullTestId, linkTo, resolvedAriaLabel, icon)
      : renderLabelLink(fullTestId, linkTo, ariaLabel, label);

  return (
    <HoverReveal id={descriptionId} description={description}>
      {control}
    </HoverReveal>
  );
}
