import { useId, type ReactNode } from "react";

import Button, { type ButtonVariant } from "./Button";
import HoverReveal from "./HoverReveal";

export interface DescribedIconButtonProps {
  readonly glyph: ReactNode;
  readonly label: string;
  readonly description: string;
  readonly testId: string;
  readonly onClick: () => void;
  readonly align?: "start" | "end";
  readonly density?: "default" | "compact";
  readonly expanded?: boolean | undefined;
  readonly controls?: string | undefined;
  readonly className?: string;
  readonly variant?: Extract<ButtonVariant, "ghost" | "destroy">;
}

export default function DescribedIconButton({
  glyph,
  label,
  description,
  testId,
  onClick,
  align = "start",
  density = "default",
  expanded,
  controls,
  className = "",
  variant = "ghost",
}: Readonly<DescribedIconButtonProps>) {
  const descriptionId = useId();
  return (
    <HoverReveal id={descriptionId} description={description} align={align}>
      <Button
        variant={variant}
        iconOnly
        aria-label={label}
        aria-expanded={expanded}
        aria-controls={controls}
        density={density}
        className={className}
        data-testid={testId}
        onClick={onClick}
      >
        <span aria-hidden="true">{glyph}</span>
      </Button>
    </HoverReveal>
  );
}
