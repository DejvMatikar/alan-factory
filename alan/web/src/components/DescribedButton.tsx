import { useId } from "react";

import Button from "./Button";
import HoverReveal from "./HoverReveal";

export interface DescribedButtonProps {
  readonly variant: "ghost" | "destroy";
  readonly label: string;
  readonly description: string;
  readonly testId: string;
  readonly onClick: () => void;
  readonly busy?: boolean;
  readonly ariaLabel?: string;
  readonly align?: "start" | "end";
}

export default function DescribedButton({
  variant,
  label,
  description,
  testId,
  onClick,
  busy = false,
  ariaLabel,
  align = "start",
}: Readonly<DescribedButtonProps>) {
  const descriptionId = useId();
  return (
    <HoverReveal id={descriptionId} description={description} align={align}>
      <Button
        variant={variant}
        aria-label={ariaLabel}
        busy={busy}
        data-testid={testId}
        onClick={onClick}
      >
        {label}
      </Button>
    </HoverReveal>
  );
}
